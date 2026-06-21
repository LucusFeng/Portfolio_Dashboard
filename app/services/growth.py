from dataclasses import dataclass
from typing import List, Optional

import sqlite3

from app.repository.observations import latest_fx_rate
from app.repository.portfolio import contribution_cashflows
from app.services.portfolio import to_cad


@dataclass(frozen=True)
class GrowthPoint:
    date: str
    cumulative_contributions_cad: Optional[float]


def get_value_vs_contributions(conn: sqlite3.Connection) -> List[GrowthPoint]:
    usdcad = latest_fx_rate(conn)
    total = 0.0
    points: List[GrowthPoint] = []
    for row in contribution_cashflows(conn):
        amount = to_cad(float(row["amount"]), row["currency"], usdcad)
        if amount is None:
            points.append(GrowthPoint(row["txn_date"], None))
            continue
        total += amount
        points.append(GrowthPoint(row["txn_date"], total))
    return points
