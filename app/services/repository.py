import sqlite3
from typing import Iterable, Optional

from app.services.flex import ParsedPosition


def upsert_account(conn: sqlite3.Connection, broker: str, external_id: str, label: str) -> int:
    conn.execute(
        """
        INSERT INTO accounts (broker, external_id, label)
        VALUES (?, ?, ?)
        ON CONFLICT(broker, external_id) DO UPDATE SET label = excluded.label
        """,
        (broker, external_id, label),
    )
    row = conn.execute(
        "SELECT id FROM accounts WHERE broker = ? AND external_id = ?",
        (broker, external_id),
    ).fetchone()
    return int(row["id"])


def upsert_instrument(
    conn: sqlite3.Connection,
    asset_class: str,
    symbol: str,
    name: str,
    currency: str,
    conid: Optional[str],
) -> int:
    if conid:
        existing = conn.execute(
            "SELECT id FROM instruments WHERE conid = ?",
            (conid,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE instruments SET symbol = ?, name = ?, currency = ?, asset_class = ? WHERE id = ?",
                (symbol, name, currency, asset_class, existing["id"]),
            )
            return int(existing["id"])

    conn.execute(
        """
        INSERT INTO instruments (asset_class, symbol, name, currency, conid)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(symbol, currency, asset_class) DO UPDATE SET
            name = excluded.name,
            conid = COALESCE(excluded.conid, instruments.conid)
        """,
        (asset_class, symbol, name, currency, conid),
    )
    row = conn.execute(
        """
        SELECT id FROM instruments
        WHERE symbol = ? AND currency = ? AND asset_class = ?
        """,
        (symbol, currency, asset_class),
    ).fetchone()
    return int(row["id"])


def upsert_alias(
    conn: sqlite3.Connection,
    instrument_id: int,
    broker: str,
    local_symbol: str,
    currency: str,
    conid: Optional[str],
) -> None:
    conn.execute(
        """
        INSERT INTO instrument_aliases (instrument_id, broker, local_symbol, currency, conid)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(broker, local_symbol, currency) DO UPDATE SET
            instrument_id = excluded.instrument_id,
            conid = COALESCE(excluded.conid, instrument_aliases.conid)
        """,
        (instrument_id, broker, local_symbol, currency, conid),
    )


def append_positions(
    conn: sqlite3.Connection,
    positions: Iterable[ParsedPosition],
    snapshot_date: str,
    source: str,
) -> int:
    count = 0
    for position in positions:
        account_id = upsert_account(
            conn,
            "IBKR",
            position.account_external_id,
            position.account_label,
        )
        instrument_id = upsert_instrument(
            conn,
            position.asset_class,
            position.symbol,
            position.name,
            position.currency,
            position.conid,
        )
        upsert_alias(
            conn,
            instrument_id,
            "IBKR",
            position.symbol,
            position.currency,
            position.conid,
        )
        conn.execute(
            """
            INSERT INTO position_snapshots
                (snapshot_date, account_id, instrument_id, quantity, avg_cost, currency, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_date,
                account_id,
                instrument_id,
                position.quantity,
                position.avg_cost,
                position.currency,
                source,
            ),
        )
        count += 1
    return count


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
        """,
        (instrument_id, as_of, price, currency, source),
    )


def append_fx_rate(conn: sqlite3.Connection, pair: str, as_of: str, rate: float, source: str) -> None:
    conn.execute(
        """
        INSERT INTO fx_rates (pair, as_of, rate, source)
        VALUES (?, ?, ?, ?)
        """,
        (pair, as_of, rate, source),
    )
