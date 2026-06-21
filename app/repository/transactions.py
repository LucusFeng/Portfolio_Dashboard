import sqlite3
from typing import Iterable, Optional

from app.models import ParsedTransaction
from app.repository.instruments import upsert_account, upsert_alias, upsert_instrument


def append_transaction(conn: sqlite3.Connection, parsed: ParsedTransaction) -> bool:
    account_id = upsert_account(
        conn,
        parsed.broker,
        parsed.account_external_id,
        parsed.account_label,
        parsed.tax_type,
    )
    instrument_id: Optional[int] = None
    if parsed.instrument is not None:
        instrument_id = upsert_instrument(conn, parsed.instrument)
        upsert_alias(conn, parsed.broker, parsed.instrument.symbol, instrument_id)

    try:
        conn.execute(
            """
            INSERT INTO transactions
                (txn_date, account_id, instrument_id, txn_type, quantity, price,
                 amount, currency, source, external_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parsed.txn_date,
                account_id,
                instrument_id,
                parsed.txn_type,
                parsed.quantity,
                parsed.price,
                parsed.amount,
                parsed.currency,
                parsed.source,
                parsed.external_id,
            ),
        )
        return True
    except sqlite3.IntegrityError:
        if parsed.external_id:
            return False
        raise


def append_transactions(conn: sqlite3.Connection, transactions: Iterable[ParsedTransaction]) -> int:
    inserted = 0
    for parsed in transactions:
        if append_transaction(conn, parsed):
            inserted += 1
    return inserted


def count_transactions(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM transactions").fetchone()
    return int(row["count"])
