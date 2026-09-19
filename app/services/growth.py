from dataclasses import dataclass
from typing import List, Optional

import sqlite3

from app.repository.nav import daily_nav_series
from app.repository.observations import latest_fx_rate
from app.repository.portfolio import contribution_cashflows
from app.services.portfolio import to_cad


@dataclass(frozen=True)
class GrowthPoint:
    date: str
    cumulative_contributions_cad: Optional[float]
    portfolio_value_cad: Optional[float] = None


def get_value_vs_contributions(conn: sqlite3.Connection) -> List[GrowthPoint]:
    usdcad = latest_fx_rate(conn)
    nav_by_date = {}
    for row in daily_nav_series(conn):
        nav_by_date[row["report_date"]] = nav_by_date.get(row["report_date"], 0.0) + float(row["nav"])

    flows_by_date = {}
    missing_flow_dates = set()
    for row in contribution_cashflows(conn):
        amount = to_cad(float(row["amount"]), row["currency"], usdcad)
        if amount is None:
            missing_flow_dates.add(row["txn_date"])
            continue
        flows_by_date[row["txn_date"]] = flows_by_date.get(row["txn_date"], 0.0) + amount

    if not nav_by_date:
        total = 0.0
        points: List[GrowthPoint] = []
        for date in sorted(set(flows_by_date) | missing_flow_dates):
            if date in missing_flow_dates:
                points.append(GrowthPoint(date, None))
                continue
            total += flows_by_date.get(date, 0.0)
            points.append(GrowthPoint(date, total))
        return points

    total = 0.0
    points = []
    for date in sorted(set(nav_by_date) | set(flows_by_date) | missing_flow_dates):
        if date in missing_flow_dates:
            points.append(GrowthPoint(date, None, nav_by_date.get(date)))
            continue
        total += flows_by_date.get(date, 0.0)
        points.append(GrowthPoint(date, total, nav_by_date.get(date)))
    return points
