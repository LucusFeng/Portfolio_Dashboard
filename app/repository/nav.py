import sqlite3
from typing import Iterable, Optional

from app.models import ParsedChangeInNav, ParsedDailyNav
from app.repository.instruments import upsert_account


def upsert_daily_nav(
    conn: sqlite3.Connection,
    rows: Iterable[ParsedDailyNav],
    content_hash: Optional[str] = None,
    ingested_at: Optional[str] = None,
) -> int:
    count = 0
    for parsed in rows:
        account_id = upsert_account(
            conn,
            "IBKR",
            parsed.account_external_id,
            parsed.account_label,
            "UNKNOWN",
            parsed.currency,
        )
        conn.execute(
            """
            INSERT INTO daily_nav
                (account_id, report_date, nav, cash, stock, dividend_accruals,
                 interest_accruals, currency, content_hash, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, report_date) DO UPDATE SET
                nav = excluded.nav,
                cash = excluded.cash,
                stock = excluded.stock,
                dividend_accruals = excluded.dividend_accruals,
                interest_accruals = excluded.interest_accruals,
                currency = excluded.currency,
                content_hash = excluded.content_hash,
                ingested_at = excluded.ingested_at
            """,
            (
                account_id,
                parsed.report_date,
                parsed.nav,
                parsed.cash,
                parsed.stock,
                parsed.dividend_accruals,
                parsed.interest_accruals,
                parsed.currency,
                content_hash,
                ingested_at,
            ),
        )
        count += 1
    return count


def upsert_nav_summary(
    conn: sqlite3.Connection,
    rows: Iterable[ParsedChangeInNav],
    content_hash: Optional[str] = None,
    ingested_at: Optional[str] = None,
) -> int:
    count = 0
    for parsed in rows:
        account_id = upsert_account(
            conn,
            "IBKR",
            parsed.account_external_id,
            parsed.account_label,
            "UNKNOWN",
            parsed.currency,
        )
        conn.execute(
            """
            INSERT INTO nav_summary
                (account_id, from_date, to_date, twr, starting_value, ending_value,
                 deposits_withdrawals, dividends, mtm, interest, realized,
                 change_in_unrealized, change_in_dividend_accruals, broker_fees,
                 forex_commissions, fx_translation, cost_adjustments, currency,
                 content_hash, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, to_date) DO UPDATE SET
                from_date = excluded.from_date,
                twr = excluded.twr,
                starting_value = excluded.starting_value,
                ending_value = excluded.ending_value,
                deposits_withdrawals = excluded.deposits_withdrawals,
                dividends = excluded.dividends,
                mtm = excluded.mtm,
                interest = excluded.interest,
                realized = excluded.realized,
                change_in_unrealized = excluded.change_in_unrealized,
                change_in_dividend_accruals = excluded.change_in_dividend_accruals,
                broker_fees = excluded.broker_fees,
                forex_commissions = excluded.forex_commissions,
                fx_translation = excluded.fx_translation,
                cost_adjustments = excluded.cost_adjustments,
                currency = excluded.currency,
                content_hash = excluded.content_hash,
                ingested_at = excluded.ingested_at
            """,
            (
                account_id,
                parsed.from_date,
                parsed.to_date,
                parsed.twr,
                parsed.starting_value,
                parsed.ending_value,
                parsed.deposits_withdrawals,
                parsed.dividends,
                parsed.mtm,
                parsed.interest,
                parsed.realized,
                parsed.change_in_unrealized,
                parsed.change_in_dividend_accruals,
                parsed.broker_fees,
                parsed.forex_commissions,
                parsed.fx_translation,
                parsed.cost_adjustments,
                parsed.currency,
                content_hash,
                ingested_at,
            ),
        )
        count += 1
    return count


def latest_nav_summary(conn: sqlite3.Connection, account_id: Optional[int] = None):
    params = []
    account_filter = ""
    if account_id is not None:
        account_filter = "WHERE ns.account_id = ?"
        params.append(account_id)
    return conn.execute(
        """
        WITH latest AS (
            SELECT account_id, MAX(to_date) AS to_date
            FROM nav_summary
            GROUP BY account_id
        )
        SELECT ns.*, a.label AS account_label, a.external_id AS account_external_id
        FROM nav_summary ns
        JOIN latest l ON l.account_id = ns.account_id AND l.to_date = ns.to_date
        JOIN accounts a ON a.id = ns.account_id
        %s
        ORDER BY a.label
        """ % account_filter,
        params,
    ).fetchall()


def daily_nav_series(conn: sqlite3.Connection, account_id: Optional[int] = None):
    params = []
    account_filter = ""
    if account_id is not None:
        account_filter = "WHERE dn.account_id = ?"
        params.append(account_id)
    return conn.execute(
        """
        SELECT dn.*, a.label AS account_label, a.external_id AS account_external_id
        FROM daily_nav dn
        JOIN accounts a ON a.id = dn.account_id
        %s
        ORDER BY dn.report_date, a.label
        """ % account_filter,
        params,
    ).fetchall()
