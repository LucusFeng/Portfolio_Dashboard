import sqlite3

from app.ingestion.reference_data import ReferenceProvider
from app.repository.instruments import instruments_missing_reference, update_reference


def enrich_missing_instruments(conn: sqlite3.Connection, provider: ReferenceProvider) -> int:
    count = 0
    for row in instruments_missing_reference(conn):
        ref = provider.fetch_reference(row["symbol"])
        if ref is None:
            continue
        update_reference(conn, row["id"], ref)
        count += 1
    return count
