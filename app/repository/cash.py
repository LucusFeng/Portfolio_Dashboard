import sqlite3
from typing import Iterable

from app.models import ParsedCashReport
from app.repository.instruments import upsert_account


def upsert_cash_balances(
    conn: sqlite3.Connection,
    cash_reports: Iterable[ParsedCashReport],
    snapshot_date: str,
    source: str,
) -> int:
    count = 0
    for report in cash_reports:
        account_id = upsert_account(conn, "IBKR", report.account_external_id, report.account_label)
        conn.execute(
            """
            INSERT INTO cash_balances
                (snapshot_date, account_id, currency, ending_cash, deposits, withdrawals, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date, account_id, currency) DO UPDATE SET
                ending_cash = excluded.ending_cash,
                deposits = excluded.deposits,
                withdrawals = excluded.withdrawals,
                source = excluded.source
            """,
            (
                snapshot_date,
                account_id,
                report.currency,
                report.ending_cash,
                report.deposits,
                report.withdrawals,
                source,
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


def derived_cash_balance(conn: sqlite3.Connection, account_id: int, currency: str) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS balance
        FROM transactions
        WHERE account_id = ? AND currency = ?
        """,
        (account_id, currency),
    ).fetchone()
    return float(row["balance"] or 0.0)


def derived_contributions(conn: sqlite3.Connection, account_id: int, currency: str) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS contributions
        FROM transactions
        WHERE account_id = ?
          AND currency = ?
          AND txn_type IN ('DEPOSIT', 'WITHDRAWAL')
        """,
        (account_id, currency),
    ).fetchone()
    return float(row["contributions"] or 0.0)


def record_cash_reconciliation(
    conn: sqlite3.Connection,
    cash_reports: Iterable[ParsedCashReport],
    snapshot_date: str,
    source: str,
) -> int:
    count = 0
    for report in cash_reports:
        account_id = upsert_account(conn, "IBKR", report.account_external_id, report.account_label)
        checks = [
            ("balance", report.ending_cash, derived_cash_balance(conn, account_id, report.currency)),
            (
                "contributions",
                report.deposits + report.withdrawals,
                derived_contributions(conn, account_id, report.currency),
            ),
        ]
        for check_type, broker_value, derived_value in checks:
            difference = float(broker_value) - float(derived_value)
            conn.execute(
                """
                INSERT INTO cash_reconciliations
                    (snapshot_date, account_id, currency, check_type, broker_value,
                     derived_value, difference, status, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_date,
                    account_id,
                    report.currency,
                    check_type,
                    broker_value,
                    derived_value,
                    difference,
                    "ok" if abs(difference) <= 1.0 else "mismatch",
                    source,
                ),
            )
            count += 1
    return count


def latest_cash_reconciliation_warnings(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT
            a.label AS account_label,
            c.currency,
            c.check_type,
            c.broker_value,
            c.derived_value,
            c.difference
        FROM cash_reconciliations c
        JOIN accounts a ON a.id = c.account_id
        WHERE c.status != 'ok'
        ORDER BY c.created_at DESC, a.label, c.currency, c.check_type
        LIMIT 20
        """
    ).fetchall()
