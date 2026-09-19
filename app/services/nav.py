from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Tuple

import sqlite3

from app.repository.nav import daily_nav_series


@dataclass(frozen=True)
class NavPoint:
    date: str
    nav: float


@dataclass(frozen=True)
class CashFlowPoint:
    date: str
    amount: float


def compute_twr(
    nav_series: Iterable[Tuple[str, float]],
    cash_flows: Iterable[Tuple[str, float]],
    flow_timing: str = "end_of_day",
) -> Optional[float]:
    flows = _flow_map(cash_flows)
    linked_return = 1.0
    previous_nav: Optional[float] = None
    used_periods = 0

    for date, nav in sorted((date, float(nav)) for date, nav in nav_series):
        flow = flows.get(date, 0.0)
        if previous_nav is None:
            previous_nav = nav
            continue
        if abs(previous_nav) <= 1e-9:
            previous_nav = nav
            continue
        if flow_timing == "start_of_day":
            denominator = previous_nav + flow
            if abs(denominator) <= 1e-9:
                previous_nav = nav
                continue
            period_return = nav / denominator - 1.0
        else:
            period_return = (nav - flow) / previous_nav - 1.0
        linked_return *= 1.0 + period_return
        used_periods += 1
        previous_nav = nav

    if used_periods == 0:
        return None
    return (linked_return - 1.0) * 100.0


def get_consolidated_twr(conn: sqlite3.Connection) -> Optional[float]:
    nav_by_date = {}
    for row in daily_nav_series(conn):
        nav_by_date[row["report_date"]] = nav_by_date.get(row["report_date"], 0.0) + float(row["nav"])
    if not nav_by_date:
        return None
    flows = [
        (row["flow_date"], float(row["amount_cad"]))
        for row in contribution_cashflows_cad(conn)
        if row["amount_cad"] is not None
    ]
    return compute_twr(sorted(nav_by_date.items()), flows)


def contribution_cashflows_cad(conn: sqlite3.Connection, account_id: Optional[int] = None):
    params: List[object] = []
    account_filter = ""
    if account_id is not None:
        account_filter = "AND t.account_id = ?"
        params.append(account_id)
    return conn.execute(
        """
        SELECT
            COALESCE(t.available_date, t.report_date, t.txn_date) AS flow_date,
            SUM(
                CASE
                    WHEN t.currency = 'CAD' THEN t.amount
                    WHEN t.currency = 'USD' THEN t.amount * COALESCE((
                        SELECT pv.fx_rate_to_base
                        FROM position_values pv
                        WHERE pv.fx_rate_to_base IS NOT NULL
                          AND pv.native_currency = 'USD'
                        ORDER BY pv.snapshot_date DESC, pv.id DESC
                        LIMIT 1
                    ), (
                        SELECT fr.rate
                        FROM fx_rates fr
                        WHERE fr.pair = 'USDCAD'
                        ORDER BY fr.as_of DESC
                        LIMIT 1
                    ))
                    ELSE NULL
                END
            ) AS amount_cad
        FROM transactions t
        WHERE t.txn_type IN ('DEPOSIT', 'WITHDRAWAL')
          %s
        GROUP BY COALESCE(t.available_date, t.report_date, t.txn_date)
        ORDER BY flow_date
        """ % account_filter,
        params,
    ).fetchall()


def _flow_map(cash_flows: Iterable[Tuple[str, float]]) -> Mapping[str, float]:
    flows = {}
    for date, amount in cash_flows:
        flows[date] = flows.get(date, 0.0) + float(amount)
    return flows
