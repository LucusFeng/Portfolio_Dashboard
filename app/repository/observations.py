import sqlite3
from typing import Optional


def append_price(
    conn: sqlite3.Connection,
    instrument_id: int,
    as_of: str,
    price: float,
    currency: str,
    source: str,
) -> None:
    conn.execute(
        """
        INSERT INTO prices (instrument_id, as_of, price, currency, source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(instrument_id, as_of) DO UPDATE SET
            price = excluded.price,
            currency = excluded.currency,
            source = excluded.source
        """,
        (instrument_id, as_of, price, currency, source),
    )


def append_fx_rate(conn: sqlite3.Connection, pair: str, as_of: str, rate: float, source: str) -> None:
    conn.execute(
        """
        INSERT INTO fx_rates (pair, as_of, rate, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(pair, as_of) DO UPDATE SET
            rate = excluded.rate,
            source = excluded.source
        """,
        (pair, as_of, rate, source),
    )


def latest_fx_rate(conn: sqlite3.Connection, pair: str = "USDCAD") -> Optional[float]:
    row = conn.execute(
        "SELECT rate FROM fx_rates WHERE pair = ? ORDER BY as_of DESC LIMIT 1",
        (pair,),
    ).fetchone()
    return float(row["rate"]) if row else None


def instruments_for_price_refresh(conn: sqlite3.Connection):
    return conn.execute(
        """
        WITH latest_date AS (
            SELECT account_id, instrument_id, MAX(snapshot_date) AS snapshot_date
            FROM positions
            GROUP BY account_id, instrument_id
        )
        SELECT DISTINCT i.id, i.conid, i.currency
        FROM positions pos
        JOIN latest_date d
          ON d.account_id = pos.account_id
         AND d.instrument_id = pos.instrument_id
         AND d.snapshot_date = pos.snapshot_date
        JOIN accounts a ON a.id = pos.account_id
        JOIN instruments i ON i.id = pos.instrument_id
        WHERE a.broker != 'IBKR'
          AND i.asset_class != 'CASH'
          AND i.conid IS NOT NULL
          AND i.conid != ''
        ORDER BY i.symbol
        """
    ).fetchall()
