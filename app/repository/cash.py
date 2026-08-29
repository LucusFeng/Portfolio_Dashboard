import sqlite3
from typing import Iterable, Optional

from app.models import ParsedCashReport
from app.repository.instruments import upsert_account


def upsert_cash_balances(
    conn: sqlite3.Connection,
    cash_reports: Iterable[ParsedCashReport],
    snapshot_date: str,
    source: str,
    statement_generated_at: Optional[str] = None,
    ingested_at: Optional[str] = None,
    content_hash: Optional[str] = None,
) -> int:
    count = 0
    for report in cash_reports:
        account_id = upsert_account(conn, "IBKR", report.account_external_id, report.account_label)
        conn.execute(
            """
            INSERT INTO cash_balances
                (snapshot_date, account_id, currency, ending_cash, deposits, withdrawals, source,
                 statement_generated_at, ingested_at, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date, account_id, currency) DO UPDATE SET
                ending_cash = excluded.ending_cash,
                deposits = excluded.deposits,
                withdrawals = excluded.withdrawals,
                source = excluded.source,
                statement_generated_at = excluded.statement_generated_at,
                ingested_at = excluded.ingested_at,
                content_hash = excluded.content_hash
            """,
            (
                snapshot_date,
                account_id,
                report.currency,
                report.ending_cash,
                report.deposits,
                report.withdrawals,
                source,
                statement_generated_at,
                ingested_at,
                content_hash,
            ),
        )
        count += 1
    return count


def latest_cash_balances(conn: sqlite3.Connection):
    return conn.execute(
        """
        WITH latest AS (
            SELECT account_id, currency, MAX(snapshot_date) AS snapshot_date
            FROM cash_balances
            GROUP BY account_id, currency
        )
        SELECT
            a.id AS account_id,
            a.label AS account_label,
            c.currency,
            c.ending_cash,
            c.deposits,
            c.withdrawals
        FROM cash_balances c
        JOIN latest l
          ON l.account_id = c.account_id
         AND l.currency = c.currency
         AND l.snapshot_date = c.snapshot_date
        JOIN accounts a ON a.id = c.account_id
        WHERE ABS(c.ending_cash) > 1e-9
        ORDER BY a.label, c.currency
        """
    ).fetchall()


def latest_fx_rate_to_base(conn: sqlite3.Connection):
    row = conn.execute(
        """
        SELECT fx_rate_to_base
        FROM position_values
        WHERE fx_rate_to_base IS NOT NULL
          AND UPPER(native_currency) = 'USD'
        ORDER BY snapshot_date DESC, created_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return float(row["fx_rate_to_base"])
