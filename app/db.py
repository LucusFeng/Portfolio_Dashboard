import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker TEXT NOT NULL,
    external_id TEXT NOT NULL,
    label TEXT NOT NULL,
    tax_type TEXT NOT NULL DEFAULT 'UNKNOWN',
    base_currency TEXT NOT NULL DEFAULT 'CAD',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (broker, external_id)
);

CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_class TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    currency TEXT NOT NULL,
    conid TEXT,
    isin TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (symbol, currency, asset_class)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_instruments_conid
ON instruments(conid)
WHERE conid IS NOT NULL AND conid != '';

CREATE TABLE IF NOT EXISTS instrument_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    broker TEXT NOT NULL,
    local_symbol TEXT NOT NULL,
    currency TEXT NOT NULL,
    conid TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (broker, local_symbol, currency)
);

CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    quantity REAL NOT NULL,
    avg_cost REAL,
    currency TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_positions_latest
ON position_snapshots(account_id, instrument_id, snapshot_date, id);

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    as_of TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prices_latest
ON prices(instrument_id, as_of, id);

CREATE TABLE IF NOT EXISTS fx_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    as_of TEXT NOT NULL,
    rate REAL NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fx_latest
ON fx_rates(pair, as_of, id);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);
"""


def connect(database_path: str) -> sqlite3.Connection:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
