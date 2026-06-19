import sqlite3

from app.db import init_db
from app.services.flex import ParsedPosition
from app.services.repository import append_fx_rate, append_positions, append_price
from app.services.valuation import build_dashboard_data


def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_append_only_positions_and_latest_values_are_used():
    conn = memory_db()
    position = ParsedPosition(
        account_external_id="U111111",
        account_label="RRSP",
        asset_class="STK",
        symbol="AAPL",
        name="APPLE INC",
        currency="USD",
        quantity=10,
        avg_cost=150,
        conid="265598",
    )

    append_positions(conn, [position], "2026-06-14", "test")
    append_positions(conn, [position], "2026-06-15", "test")
    instrument_id = conn.execute("SELECT id FROM instruments WHERE symbol = 'AAPL'").fetchone()["id"]
    append_price(conn, instrument_id, "2026-06-15", 200, "USD", "test")
    append_fx_rate(conn, "USDCAD", "2026-06-15", 1.35, "test")
    conn.commit()

    assert conn.execute("SELECT COUNT(*) AS c FROM position_snapshots").fetchone()["c"] == 2

    data = build_dashboard_data(conn)

    assert len(data.holdings) == 1
    assert data.holdings[0].market_value == 2000
    assert data.holdings[0].market_value_cad == 2700
    assert data.holdings[0].unrealized_pnl == 500
    assert data.grand_total_cad == 2700


def test_cash_is_valued_at_one_and_converted_to_cad():
    conn = memory_db()
    cash = ParsedPosition(
        account_external_id="U111111",
        account_label="RRSP",
        asset_class="CASH",
        symbol="CASH:USD",
        name="USD cash",
        currency="USD",
        quantity=100,
        avg_cost=1,
        conid=None,
    )

    append_positions(conn, [cash], "2026-06-15", "test")
    append_fx_rate(conn, "USDCAD", "2026-06-15", 1.25, "test")
    conn.commit()

    data = build_dashboard_data(conn)

    assert data.holdings[0].price == 1
    assert data.holdings[0].market_value_cad == 125
