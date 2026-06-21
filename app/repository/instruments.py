import datetime as dt
import json
import sqlite3
from typing import Optional

from app.models import InstrumentRef, ParsedInstrument


def normalize_asset_class(value: str) -> str:
    normalized = (value or "").upper()
    if normalized in {"STK", "STOCK", "EQUITY", "COMMON STOCK"}:
        return "EQUITY"
    if normalized in {"ETF", "EXCHANGE TRADED FUND"}:
        return "ETF"
    if normalized in {"CASH", "CASHREPORT"}:
        return "CASH"
    if normalized in {"OPT", "OPTION"}:
        return "OPTION"
    return normalized or "UNKNOWN"


def upsert_account(
    conn: sqlite3.Connection,
    broker: str,
    external_id: Optional[str],
    label: str,
    tax_type: str = "UNKNOWN",
    base_currency: str = "CAD",
) -> int:
    conn.execute(
        """
        INSERT INTO accounts (broker, external_id, label, tax_type, base_currency)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(broker, external_id) DO UPDATE SET
            label = excluded.label,
            tax_type = excluded.tax_type,
            base_currency = excluded.base_currency
        """,
        (broker, external_id, label, tax_type, base_currency),
    )
    row = conn.execute(
        "SELECT id FROM accounts WHERE broker = ? AND external_id IS ?",
        (broker, external_id),
    ).fetchone()
    return int(row["id"])


def upsert_instrument(conn: sqlite3.Connection, instrument: ParsedInstrument) -> int:
    asset_class = normalize_asset_class(instrument.asset_class)
    symbol = instrument.symbol.upper()
    if instrument.conid:
        existing = conn.execute(
            "SELECT id FROM instruments WHERE conid = ?",
            (instrument.conid,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE instruments
                SET symbol = ?, name = COALESCE(?, name), asset_class = ?, currency = ?,
                    isin = COALESCE(?, isin)
                WHERE id = ?
                """,
                (symbol, instrument.name, asset_class, instrument.currency, instrument.isin, existing["id"]),
            )
            return int(existing["id"])

    conn.execute(
        """
        INSERT INTO instruments (symbol, name, asset_class, currency, conid, isin)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, currency, asset_class) DO UPDATE SET
            name = COALESCE(excluded.name, instruments.name),
            conid = COALESCE(excluded.conid, instruments.conid),
            isin = COALESCE(excluded.isin, instruments.isin)
        """,
        (symbol, instrument.name, asset_class, instrument.currency, instrument.conid, instrument.isin),
    )
    row = conn.execute(
        """
        SELECT id FROM instruments
        WHERE symbol = ? AND currency = ? AND asset_class = ?
        """,
        (symbol, instrument.currency, asset_class),
    ).fetchone()
    return int(row["id"])


def upsert_alias(conn: sqlite3.Connection, broker: str, broker_symbol: str, instrument_id: int) -> None:
    conn.execute(
        """
        INSERT INTO instrument_aliases (broker, broker_symbol, instrument_id)
        VALUES (?, ?, ?)
        ON CONFLICT(broker, broker_symbol) DO UPDATE SET instrument_id = excluded.instrument_id
        """,
        (broker, broker_symbol.upper(), instrument_id),
    )


def instruments_missing_reference(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT id, symbol
        FROM instruments
        WHERE asset_class IN ('EQUITY', 'ETF')
          AND symbol IS NOT NULL
          AND (ref_as_of IS NULL OR ref_source IS NULL)
        ORDER BY symbol
        """
    ).fetchall()


def update_reference(conn: sqlite3.Connection, instrument_id: int, ref: InstrumentRef) -> None:
    conn.execute(
        """
        UPDATE instruments
        SET sector = ?, industry = ?, country = ?, market_cap = ?,
            name = COALESCE(?, name),
            attributes = ?,
            ref_source = ?,
            ref_as_of = ?
        WHERE id = ?
        """,
        (
            ref.sector,
            ref.industry,
            ref.country,
            ref.market_cap,
            ref.name,
            json.dumps(ref.extra, sort_keys=True),
            ref.source,
            dt.datetime.utcnow().isoformat(timespec="seconds"),
            instrument_id,
        ),
    )
