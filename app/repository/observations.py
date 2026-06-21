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
        SELECT id, conid, currency
        FROM instruments
        WHERE asset_class != 'CASH' AND conid IS NOT NULL AND conid != ''
        ORDER BY symbol
        """
    ).fetchall()
