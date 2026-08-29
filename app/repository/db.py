import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 8

TABLES = [
    "evidence_store",
    "cash_balances",
    "reconciliations",
    "ingestion_runs",
    "fx_rates",
    "prices",
    "position_values",
    "positions",
    "position_snapshots",
    "lots",
    "transactions",
    "instrument_aliases",
    "instruments",
    "accounts",
]

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker TEXT NOT NULL,
    external_id TEXT,
    label TEXT NOT NULL,
    tax_type TEXT NOT NULL DEFAULT 'UNKNOWN',
    base_currency TEXT NOT NULL DEFAULT 'CAD',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (broker, external_id)
);

CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    name TEXT,
    asset_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    conid TEXT,
    isin TEXT,
    figi TEXT,
    sector TEXT,
    industry TEXT,
    country TEXT,
    market_cap REAL,
    underlying_instrument_id INTEGER REFERENCES instruments(id),
    attributes TEXT,
    ref_source TEXT,
    ref_as_of TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (symbol, currency, asset_class)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_instruments_conid
ON instruments(conid)
WHERE conid IS NOT NULL AND conid != '';

CREATE TABLE IF NOT EXISTS instrument_aliases (
    broker TEXT NOT NULL,
    broker_symbol TEXT NOT NULL,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (broker, broker_symbol)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id INTEGER REFERENCES instruments(id),
    txn_type TEXT NOT NULL,
    quantity REAL,
    price REAL,
    trade_cost REAL,
    commission REAL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_external_id
ON transactions(source, external_id)
WHERE external_id IS NOT NULL AND external_id != '';

CREATE INDEX IF NOT EXISTS idx_transactions_account_date
ON transactions(account_id, txn_date, id);

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    open_date TEXT NOT NULL,
    open_quantity REAL NOT NULL,
    remaining_qty REAL NOT NULL,
    cost_basis REAL NOT NULL,
    remaining_cost_basis REAL NOT NULL,
    cost_per_unit REAL NOT NULL,
    cost_currency TEXT NOT NULL,
    open_txn_id INTEGER REFERENCES transactions(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lots_open
ON lots(account_id, instrument_id, open_date, id);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    quantity REAL NOT NULL,
    avg_cost REAL NOT NULL,
    cost_currency TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'derived_transactions',
    statement_generated_at TEXT,
    ingested_at TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (snapshot_date, account_id, instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_positions_latest
ON positions(account_id, instrument_id, snapshot_date, id);

CREATE TABLE IF NOT EXISTS prices (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    as_of TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument_id, as_of)
);

CREATE TABLE IF NOT EXISTS position_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    value_native REAL NOT NULL,
    value_base REAL NOT NULL,
    native_currency TEXT NOT NULL,
    fx_rate_to_base REAL,
    mark_price REAL,
    cost_basis_price REAL,
    fifo_pnl_unrealized REAL,
    unrealized_capital_gains_pnl REAL,
    unrealized_fx_pnl REAL,
    quantity REAL NOT NULL,
    source TEXT NOT NULL,
    statement_generated_at TEXT,
    ingested_at TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (snapshot_date, account_id, instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_position_values_latest
ON position_values(account_id, instrument_id, snapshot_date, id);

CREATE TABLE IF NOT EXISTS fx_rates (
    pair TEXT NOT NULL,
    as_of TEXT NOT NULL,
    rate REAL NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (pair, as_of)
);

CREATE TABLE IF NOT EXISTS reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    broker_quantity REAL NOT NULL,
    derived_quantity REAL NOT NULL,
    difference REAL NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    statement_generated_at TEXT,
    ingested_at TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cash_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    currency TEXT NOT NULL,
    ending_cash REAL NOT NULL,
    deposits REAL NOT NULL DEFAULT 0,
    withdrawals REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    statement_generated_at TEXT,
    ingested_at TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (snapshot_date, account_id, currency)
);

CREATE TABLE IF NOT EXISTS evidence_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    ingest_kind TEXT NOT NULL,
    statement_to_date TEXT,
    statement_generated_at TEXT,
    ingested_at TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    raw_size INTEGER NOT NULL,
    raw_xml_gzip BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
    conn = sqlite3.connect(database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _drop_app_tables(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in TABLES:
        conn.execute("DROP TABLE IF EXISTS %s" % table)
    conn.execute("PRAGMA foreign_keys = ON")


def init_db(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version != SCHEMA_VERSION:
        _drop_app_tables(conn)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    _drop_app_tables(conn)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
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
