import sqlite3
from typing import Iterable

from app.models import ParsedInstrument, ParsedPositionValue
from app.repository.instruments import upsert_account, upsert_alias, upsert_instrument


def upsert_position_values(
    conn: sqlite3.Connection,
    parsed_values: Iterable[ParsedPositionValue],
    snapshot_date: str,
    source: str,
) -> int:
    count = 0
    for value in parsed_values:
        account_id = upsert_account(conn, "IBKR", value.account_external_id, value.account_label)
        instrument_id = upsert_instrument(
            conn,
            ParsedInstrument(
                asset_class=value.asset_class,
                symbol=value.symbol,
                name=value.name,
                currency=value.currency,
                conid=value.conid,
            ),
        )
        upsert_alias(conn, "IBKR", value.symbol, instrument_id)
        conn.execute(
            """
            INSERT INTO position_values
                (snapshot_date, account_id, instrument_id, value_native, value_base,
                 native_currency, fx_rate_to_base, mark_price, cost_basis_price,
                 fifo_pnl_unrealized, unrealized_capital_gains_pnl, unrealized_fx_pnl,
                 quantity, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date, account_id, instrument_id) DO UPDATE SET
                value_native = excluded.value_native,
                value_base = excluded.value_base,
                native_currency = excluded.native_currency,
                fx_rate_to_base = excluded.fx_rate_to_base,
                mark_price = excluded.mark_price,
                cost_basis_price = excluded.cost_basis_price,
                fifo_pnl_unrealized = excluded.fifo_pnl_unrealized,
                unrealized_capital_gains_pnl = excluded.unrealized_capital_gains_pnl,
                unrealized_fx_pnl = excluded.unrealized_fx_pnl,
                quantity = excluded.quantity,
                source = excluded.source
            """,
            (
                snapshot_date,
                account_id,
                instrument_id,
                value.value_native,
                value.value_base,
                value.currency,
                value.fx_rate_to_base,
                value.mark_price,
                value.cost_basis_price,
                value.fifo_pnl_unrealized,
                value.unrealized_capital_gains_pnl,
                value.unrealized_fx_pnl,
                value.quantity,
                source,
            ),
        )
        count += 1
    return count


def latest_position_values(conn: sqlite3.Connection):
    return conn.execute(
        """
        WITH latest_date AS (
            SELECT account_id, instrument_id, MAX(snapshot_date) AS snapshot_date
            FROM position_values
            GROUP BY account_id, instrument_id
        )
        SELECT
            a.label AS account_label,
            i.id AS instrument_id,
            i.symbol,
            i.name,
            i.asset_class,
            pv.native_currency,
            pv.value_native,
            pv.value_base,
            pv.fx_rate_to_base,
            pv.mark_price,
            pv.cost_basis_price,
            pv.fifo_pnl_unrealized,
            pv.unrealized_capital_gains_pnl,
            pv.unrealized_fx_pnl,
            pv.quantity,
            pv.source
        FROM position_values pv
        JOIN latest_date d
          ON d.account_id = pv.account_id
         AND d.instrument_id = pv.instrument_id
         AND d.snapshot_date = pv.snapshot_date
        JOIN accounts a ON a.id = pv.account_id
        JOIN instruments i ON i.id = pv.instrument_id
        ORDER BY a.label, i.symbol
        """
    ).fetchall()
