import sqlite3
from pathlib import Path

from app.db import init_db
from app.ingestion.cibc_csv import parse_cibc_transactions
from app.ingestion.ibkr_flex import parse_flex_transactions
from app.models import ParsedCashReport, ParsedInstrument, ParsedPositionValue, ParsedTransaction
from app.repository.cash import record_cash_reconciliation, upsert_cash_balances
from app.repository.instruments import upsert_instrument
from app.repository.observations import append_fx_rate, append_price, instruments_for_price_refresh
from app.repository.position_values import latest_position_values, upsert_position_values
from app.repository.positions import rebuild_derived_state
from app.repository.transactions import append_transactions
from app.services.batch_pnl import get_batch_pnl
from app.services.cash import get_cash
from app.services.valuation import build_dashboard_data


def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_transactions_dedup_lots_positions_and_dashboard_values():
    conn = memory_db()
    transactions = [
        ParsedTransaction(
            txn_date="2026-06-10",
            broker="IBKR",
            account_external_id="U111111",
            account_label="RRSP",
            tax_type="RRSP",
            txn_type="BUY",
            quantity=10,
            price=150,
            amount=-1500,
            currency="USD",
            source="test",
            external_id="T1",
            instrument=ParsedInstrument("EQUITY", "AAPL", "APPLE INC", "USD", "265598"),
            trade_cost=1501,
        ),
        ParsedTransaction(
            txn_date="2026-06-11",
            broker="IBKR",
            account_external_id="U111111",
            account_label="RRSP",
            tax_type="RRSP",
            txn_type="SELL",
            quantity=4,
            price=175,
            amount=700,
            currency="USD",
            source="test",
            external_id="T2",
            instrument=ParsedInstrument("EQUITY", "AAPL", "APPLE INC", "USD", "265598"),
        ),
        ParsedTransaction(
            txn_date="2026-06-09",
            broker="IBKR",
            account_external_id="U111111",
            account_label="RRSP",
            tax_type="RRSP",
            txn_type="DEPOSIT",
            amount=2000,
            currency="USD",
            source="test",
            external_id="C1",
        ),
    ]

    assert append_transactions(conn, transactions) == 3
    assert append_transactions(conn, transactions) == 0
    lots, positions = rebuild_derived_state(conn, "2026-06-15")
    append_fx_rate(conn, "USDCAD", "2026-06-15", 1.35, "test")
    upsert_position_values(
        conn,
        [
            ParsedPositionValue(
                "U111111",
                "RRSP",
                "EQUITY",
                "AAPL",
                "APPLE INC",
                "USD",
                value_native=1200,
                value_base=1620,
                fx_rate_to_base=1.35,
                quantity=6,
                conid="265598",
                mark_price=200,
                fifo_pnl_unrealized=404.19,
            )
        ],
        "2026-06-15",
        "test",
    )
    upsert_cash_balances(conn, [ParsedCashReport("U111111", "RRSP", "USD", 1200)], "2026-06-15", "test")
    conn.commit()

    assert lots == 1
    assert positions == 1
    assert conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"] == 3
    assert conn.execute("SELECT remaining_qty FROM lots").fetchone()["remaining_qty"] == 6

    data = build_dashboard_data(conn)

    aapl = next(row for row in data.holdings if row.symbol == "AAPL")
    assert aapl.quantity == 6
    assert aapl.price == 200
    assert aapl.market_value == 1200
    assert aapl.market_value_cad == 1620
    assert round(aapl.cost_basis, 2) == 900.60
    assert round(aapl.avg_cost, 2) == 150.10
    assert round(aapl.unrealized_pnl, 2) == 299.40
    assert aapl.unrealized_pnl_cad == 404.19
    assert aapl.value_source == "IBKR Flex"
    assert all(not row.symbol.startswith("CASH:") for row in data.holdings)
    assert data.cash.accounts[0].usd_cash == 1200
    assert data.cash.accounts[0].usd_cash_cad == 1620
    assert data.positions_total_cad == 1620
    assert data.total_cad == 3240
    assert data.growth_points[-1].cumulative_contributions_cad == 2700


def test_flex_position_values_round_trip_and_latest_snapshot_wins():
    conn = memory_db()

    values = [
        ParsedPositionValue("U111111", "RRSP", "EQUITY", "AAPL", "APPLE INC", "USD", 1000, 1350, 1.35, 5, "265598")
    ]
    assert upsert_position_values(conn, values, "2026-06-14", "test-old") == 1
    newer = [
        ParsedPositionValue("U111111", "RRSP", "EQUITY", "AAPL", "APPLE INC", "USD", 1200, 1620, 1.35, 6, "265598")
    ]
    assert upsert_position_values(conn, newer, "2026-06-15", "test-new") == 1
    conn.commit()

    rows = latest_position_values(conn)

    assert len(rows) == 1
    assert rows[0]["account_label"] == "RRSP"
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["value_native"] == 1200
    assert rows[0]["value_base"] == 1620
    assert rows[0]["quantity"] == 6


def test_ibkr_flex_value_displays_without_derived_position_and_does_not_double_fx():
    conn = memory_db()
    append_fx_rate(conn, "USDCAD", "2026-06-15", 1.35, "test")
    upsert_position_values(
        conn,
        [ParsedPositionValue("U111111", "RRSP", "EQUITY", "AMZN", "AMAZON.COM INC", "USD", 1224.25, 1724.60, 1.4087, 5, "3691937")],
        "2026-06-15",
        "test",
    )
    conn.commit()

    data = build_dashboard_data(conn)

    amzn = data.holdings[0]
    assert amzn.symbol == "AMZN"
    assert amzn.quantity == 5
    assert amzn.derived_quantity is None
    assert amzn.price == 244.85
    assert amzn.market_value == 1224.25
    assert amzn.market_value_cad == 1724.60
    assert amzn.unrealized_pnl is None
    assert amzn.stale_reason == "missing cost basis"
    assert data.positions_total_cad == 1724.60


def test_cibc_valuation_still_uses_price_and_fx_path():
    conn = memory_db()
    append_transactions(
        conn,
        [
            ParsedTransaction(
                txn_date="2026-06-10",
                broker="CIBC",
                account_external_id="TFSA",
                account_label="TFSA",
                tax_type="TFSA",
                txn_type="BUY",
                quantity=10,
                price=100,
                amount=-1000,
                currency="USD",
                source="test",
                external_id="CIBC1",
                instrument=ParsedInstrument("EQUITY", "MSFT", "MICROSOFT CORP", "USD", "272093"),
            )
        ],
    )
    rebuild_derived_state(conn, "2026-06-15")
    instrument_id = conn.execute("SELECT id FROM instruments WHERE symbol = 'MSFT'").fetchone()["id"]
    append_price(conn, instrument_id, "2026-06-15", 120, "USD", "test")
    append_fx_rate(conn, "USDCAD", "2026-06-15", 1.25, "test")
    conn.commit()

    data = build_dashboard_data(conn)

    msft = data.holdings[0]
    assert msft.value_source == "Price"
    assert msft.market_value == 1200
    assert msft.market_value_cad == 1500
    assert msft.unrealized_pnl == 200
    assert msft.unrealized_pnl_cad == 250


def test_price_refresh_selects_only_non_ibkr_positions():
    conn = memory_db()
    append_transactions(
        conn,
        [
            ParsedTransaction(
                "2026-06-10",
                "IBKR",
                "U111111",
                "RRSP",
                "RRSP",
                "BUY",
                -100,
                "USD",
                "test",
                "IBKR1",
                ParsedInstrument("EQUITY", "AAPL", "APPLE INC", "USD", "265598"),
                1,
                100,
            ),
            ParsedTransaction(
                "2026-06-10",
                "CIBC",
                "TFSA",
                "TFSA",
                "TFSA",
                "BUY",
                -100,
                "USD",
                "test",
                "CIBC1",
                ParsedInstrument("EQUITY", "MSFT", "MICROSOFT CORP", "USD", "272093"),
                1,
                100,
            ),
        ],
    )
    rebuild_derived_state(conn, "2026-06-15")
    conn.commit()

    rows = instruments_for_price_refresh(conn)

    assert [row["conid"] for row in rows] == ["272093"]


def test_batch_pnl_uses_open_lots_and_latest_marks():
    conn = memory_db()
    append_transactions(
        conn,
        [
            ParsedTransaction(
                txn_date="2026-06-10",
                broker="IBKR",
                account_external_id="U111111",
                account_label="RRSP",
                tax_type="RRSP",
                txn_type="BUY",
                quantity=10,
                price=150,
                amount=-1500,
                currency="USD",
                source="test",
                external_id="T1",
                instrument=ParsedInstrument("EQUITY", "AAPL", "APPLE INC", "USD", "265598"),
                trade_cost=1501,
            )
        ],
    )
    rebuild_derived_state(conn, "2026-06-15")
    upsert_position_values(
        conn,
        [
            ParsedPositionValue(
                "U111111",
                "RRSP",
                "EQUITY",
                "AAPL",
                "APPLE INC",
                "USD",
                value_native=2000,
                value_base=2700,
                fx_rate_to_base=1.35,
                quantity=10,
                conid="265598",
                mark_price=200,
            )
        ],
        "2026-06-15",
        "test",
    )
    conn.commit()

    rows = get_batch_pnl(conn)

    assert len(rows) == 1
    assert rows[0].cost_basis == 1501
    assert rows[0].market_value == 2000
    assert round(rows[0].unrealized_pnl, 2) == 499


def test_v3_trade_cost_drives_total_and_batch_usd_pnl_with_flex_cad_pnl():
    conn = memory_db()
    append_transactions(
        conn,
        [
            ParsedTransaction(
                txn_date="2026-07-01",
                broker="IBKR",
                account_external_id="U111111",
                account_label="Margin",
                tax_type="UNKNOWN",
                txn_type="BUY",
                quantity=100,
                price=15.9493,
                amount=-1595.9303,
                currency="USD",
                source="test",
                external_id="SOFI1",
                instrument=ParsedInstrument("EQUITY", "SOFI", "SOFI TECHNOLOGIES INC", "USD", "481154823"),
                trade_cost=1595.9303,
                commission=-1.0,
            ),
            ParsedTransaction(
                txn_date="2026-07-02",
                broker="IBKR",
                account_external_id="U111111",
                account_label="Margin",
                tax_type="UNKNOWN",
                txn_type="BUY",
                quantity=20,
                price=16.035,
                amount=-321.70006,
                currency="USD",
                source="test",
                external_id="SOFI2",
                instrument=ParsedInstrument("EQUITY", "SOFI", "SOFI TECHNOLOGIES INC", "USD", "481154823"),
                trade_cost=321.70006,
                commission=-1.00006,
            ),
        ],
    )
    rebuild_derived_state(conn, "2026-08-01")
    upsert_position_values(
        conn,
        [
            ParsedPositionValue(
                "U111111",
                "Margin",
                "EQUITY",
                "SOFI",
                "SOFI TECHNOLOGIES INC",
                "USD",
                value_native=1957.20,
                value_base=2743.41,
                fx_rate_to_base=1.4017,
                quantity=120,
                conid="481154823",
                mark_price=16.31,
                cost_basis_price=22.1113,
                fifo_pnl_unrealized=90.05,
            )
        ],
        "2026-08-01",
        "test",
    )
    conn.commit()

    data = build_dashboard_data(conn)
    sofi = data.holdings[0]
    batches = get_batch_pnl(conn)

    assert round(sofi.avg_cost, 2) == 15.98
    assert round(sofi.cost_basis, 2) == 1917.63
    assert sofi.price == 16.31
    assert round(sofi.market_value, 2) == 1957.20
    assert round(sofi.unrealized_pnl, 2) == 39.57
    assert sofi.market_value_cad == 2743.41
    assert sofi.unrealized_pnl_cad == 90.05
    assert [round(row.cost_per_unit, 2) for row in batches] == [15.96, 16.09]
    assert round(sum(row.cost_basis for row in batches), 2) == round(sofi.cost_basis, 2)
    assert round(sum(row.market_value for row in batches), 2) == round(sofi.market_value, 2)
    assert round(sum(row.unrealized_pnl for row in batches), 2) == round(sofi.unrealized_pnl, 2)


def test_same_day_buys_merge_into_one_weighted_average_batch():
    conn = memory_db()
    append_transactions(
        conn,
        [
            ParsedTransaction(
                txn_date="2026-06-11",
                broker="IBKR",
                account_external_id="U111111",
                account_label="RRSP",
                tax_type="RRSP",
                txn_type="BUY",
                quantity=1,
                price=237.56,
                amount=-238.56,
                currency="USD",
                source="test",
                external_id="A1",
                instrument=ParsedInstrument("EQUITY", "AMZN", "AMAZON.COM INC", "USD", "3691937"),
            ),
            ParsedTransaction(
                txn_date="2026-06-11",
                broker="IBKR",
                account_external_id="U111111",
                account_label="RRSP",
                tax_type="RRSP",
                txn_type="BUY",
                quantity=2,
                price=237.49,
                amount=-475.98,
                currency="USD",
                source="test",
                external_id="A2",
                instrument=ParsedInstrument("EQUITY", "AMZN", "AMAZON.COM INC", "USD", "3691937"),
            ),
        ],
    )

    lots, positions = rebuild_derived_state(conn, "2026-06-15")
    lot = conn.execute("SELECT open_quantity, remaining_qty, cost_per_unit FROM lots").fetchone()

    assert lots == 1
    assert positions == 1
    assert lot["open_quantity"] == 3
    assert lot["remaining_qty"] == 3
    assert round(lot["cost_per_unit"], 4) == round((237.56 + 2 * 237.49) / 3, 4)


def test_cibc_csv_transactions_parse_into_canonical_model():
    csv_text = Path("tests/fixtures/cibc_transactions.csv").read_text()

    transactions = parse_cibc_transactions(csv_text)

    assert [txn.txn_type for txn in transactions] == ["DEPOSIT", "BUY", "DIVIDEND"]
    assert transactions[1].broker == "CIBC"
    assert transactions[1].instrument.symbol == "RY"


def test_cash_balance_is_read_from_cash_report_and_converted_to_cad():
    conn = memory_db()
    upsert_cash_balances(conn, [ParsedCashReport("U111111", "RRSP", "USD", -100)], "2026-06-15", "test")
    append_fx_rate(conn, "USDCAD", "2026-06-15", 1.25, "test")
    conn.commit()

    data = build_dashboard_data(conn)

    cash = data.cash.accounts[0]
    assert cash.usd_cash == -100
    assert cash.usd_cash_cad == -125
    assert cash.total_cad == -125
    assert data.holdings == []


def test_cash_balance_upsert_reads_latest_per_account_currency_and_signed_total():
    conn = memory_db()
    upsert_cash_balances(
        conn,
        [
            ParsedCashReport("U111111", "RRSP", "USD", -100),
            ParsedCashReport("U111111", "RRSP", "CAD", 50),
        ],
        "2026-06-15",
        "test",
    )
    append_fx_rate(conn, "USDCAD", "2026-06-15", 1.25, "test")
    conn.commit()

    cash = get_cash(conn)

    assert len(cash.accounts) == 1
    assert cash.accounts[0].usd_cash == -100
    assert cash.accounts[0].cad_cash == 50
    assert cash.accounts[0].usd_cash_cad == -125
    assert cash.accounts[0].total_cad == -75
    assert cash.cash_total_cad == -75


def test_cash_reconciliation_warns_only_outside_native_tolerance():
    conn = memory_db()
    append_transactions(
        conn,
        [
            ParsedTransaction(
                txn_date="2026-06-01",
                broker="IBKR",
                account_external_id="U111111",
                account_label="RRSP",
                tax_type="RRSP",
                txn_type="DEPOSIT",
                amount=100,
                currency="USD",
                source="test",
                external_id="C1",
            )
        ],
    )
    record_cash_reconciliation(
        conn,
        [ParsedCashReport("U111111", "RRSP", "USD", 100.75, deposits=100)],
        "2026-06-15",
        "test",
    )
    assert get_cash(conn).warnings == []

    record_cash_reconciliation(
        conn,
        [ParsedCashReport("U111111", "RRSP", "USD", 102.01, deposits=100)],
        "2026-06-15",
        "test",
    )
    warnings = get_cash(conn).warnings

    assert len(warnings) == 1
    assert warnings[0].check_type == "balance"
    assert warnings[0].broker_value == 102.01
    assert warnings[0].derived_value == 100
    assert round(warnings[0].difference, 2) == 2.01


def test_contributions_series_nets_offsetting_pairs_and_total_is_signed():
    conn = memory_db()
    append_transactions(
        conn,
        [
            ParsedTransaction("2026-05-01", "IBKR", "U111111", "RRSP", "RRSP", "DEPOSIT", 763.69, "CAD", "test", "C1"),
            ParsedTransaction("2026-05-01", "IBKR", "U111111", "RRSP", "RRSP", "WITHDRAWAL", -763.69, "CAD", "test", "C2"),
            ParsedTransaction("2026-05-02", "IBKR", "U111111", "RRSP", "RRSP", "DEPOSIT", 28500, "CAD", "test", "C3"),
        ],
    )
    conn.commit()

    data = build_dashboard_data(conn)

    assert data.growth_points[0].date == "2026-05-01"
    assert data.growth_points[0].cumulative_contributions_cad == 0
    assert data.growth_points[-1].cumulative_contributions_cad == 28500
    assert data.contributions_total_cad == 28500


def test_contributions_reconciliation_detects_short_date_window():
    conn = memory_db()
    append_transactions(
        conn,
        [ParsedTransaction("2026-05-01", "IBKR", "U111111", "RRSP", "RRSP", "DEPOSIT", 100, "CAD", "test", "C1")],
    )
    record_cash_reconciliation(
        conn,
        [ParsedCashReport("U111111", "RRSP", "CAD", 100, deposits=150)],
        "2026-06-15",
        "test",
    )
    warnings = get_cash(conn).warnings

    assert len(warnings) == 1
    assert warnings[0].check_type == "contributions"
    assert warnings[0].broker_value == 150
    assert warnings[0].derived_value == 100


def test_reference_data_columns_can_be_updated():
    conn = memory_db()
    instrument_id = upsert_instrument(
        conn,
        ParsedInstrument("EQUITY", "AAPL", "APPLE INC", "USD", "265598"),
    )
    columns = conn.execute("SELECT sector, attributes FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    assert columns["sector"] is None
    assert columns["attributes"] is None
