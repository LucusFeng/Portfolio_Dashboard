import sqlite3
from pathlib import Path

from app.db import init_db
from app.ingestion.cibc_csv import parse_cibc_transactions
from app.ingestion.ibkr_flex import parse_flex_transactions
from app.models import ParsedInstrument, ParsedTransaction
from app.repository.instruments import upsert_instrument
from app.repository.observations import append_fx_rate, append_price
from app.repository.positions import rebuild_derived_state
from app.repository.transactions import append_transactions
from app.services.batch_pnl import get_batch_pnl
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
    instrument_id = conn.execute("SELECT id FROM instruments WHERE symbol = 'AAPL'").fetchone()["id"]
    append_price(conn, instrument_id, "2026-06-15", 200, "USD", "test")
    append_fx_rate(conn, "USDCAD", "2026-06-15", 1.35, "test")
    conn.commit()

    assert lots == 1
    assert positions == 2
    assert conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"] == 3
    assert conn.execute("SELECT remaining_qty FROM lots").fetchone()["remaining_qty"] == 6

    data = build_dashboard_data(conn)

    aapl = next(row for row in data.holdings if row.symbol == "AAPL")
    cash = next(row for row in data.holdings if row.symbol == "CASH:USD")
    assert aapl.quantity == 6
    assert aapl.market_value == 1200
    assert aapl.market_value_cad == 1620
    assert aapl.unrealized_pnl == 300
    assert cash.market_value_cad == 1620
    assert data.growth_points[-1].cumulative_contributions_cad == 2700


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
            )
        ],
    )
    rebuild_derived_state(conn, "2026-06-15")
    instrument_id = conn.execute("SELECT id FROM instruments WHERE symbol = 'AAPL'").fetchone()["id"]
    append_price(conn, instrument_id, "2026-06-15", 200, "USD", "test")
    append_fx_rate(conn, "USDCAD", "2026-06-15", 1.35, "test")
    conn.commit()

    rows = get_batch_pnl(conn)

    assert len(rows) == 1
    assert rows[0].market_value_cad == 2700
    assert round(rows[0].unrealized_pnl_cad, 2) == 675


def test_cibc_csv_transactions_parse_into_canonical_model():
    csv_text = Path("tests/fixtures/cibc_transactions.csv").read_text()

    transactions = parse_cibc_transactions(csv_text)

    assert [txn.txn_type for txn in transactions] == ["DEPOSIT", "BUY", "DIVIDEND"]
    assert transactions[1].broker == "CIBC"
    assert transactions[1].instrument.symbol == "RY"


def test_cash_is_valued_at_one_and_converted_to_cad():
    conn = memory_db()
    append_transactions(
        conn,
        [
            ParsedTransaction(
                txn_date="2026-06-15",
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
    rebuild_derived_state(conn, "2026-06-15")
    append_fx_rate(conn, "USDCAD", "2026-06-15", 1.25, "test")
    conn.commit()

    data = build_dashboard_data(conn)

    assert data.holdings[0].price == 1
    assert data.holdings[0].market_value_cad == 125


def test_reference_data_columns_can_be_updated():
    conn = memory_db()
    instrument_id = upsert_instrument(
        conn,
        ParsedInstrument("EQUITY", "AAPL", "APPLE INC", "USD", "265598"),
    )
    columns = conn.execute("SELECT sector, attributes FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    assert columns["sector"] is None
    assert columns["attributes"] is None
