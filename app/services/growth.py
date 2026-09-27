import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.repository.nav import daily_nav_series
from app.repository.observations import latest_fx_rate
from app.repository.portfolio import contribution_events
from app.services.portfolio import to_cad


@dataclass(frozen=True)
class ContributionWealthPoint:
    date: str
    cumulative_contributions_cad: Optional[float]
    total_value_cad: Optional[float]
    change_in_value_cad: Optional[float]
    growth_pct: Optional[float]


def get_contribution_wealth_series(
    conn: sqlite3.Connection,
    max_nav_distance_days: int = 7,
) -> List[ContributionWealthPoint]:
    """Return sparse wealth-reference rows; dense daily NAV remains a separate series."""
    usdcad = latest_fx_rate(conn)
    nav_by_account = _nav_by_account(conn)
    flows_by_date: Dict[str, float] = {}
    missing_flow_dates = set()

    # This table uses cash availability semantics. TWR intentionally uses its own validated dates.
    for row in contribution_events(conn):
        amount_cad = to_cad(float(row["amount"]), row["currency"], usdcad)
        date = row["contribution_date"]
        if amount_cad is None:
            missing_flow_dates.add(date)
            continue
        flows_by_date[date] = flows_by_date.get(date, 0.0) + amount_cad

    running_contributions = 0.0
    contributions_known = True
    points: List[ContributionWealthPoint] = []
    for date in sorted(set(flows_by_date) | missing_flow_dates):
        running_contributions += flows_by_date.get(date, 0.0)
        if date in missing_flow_dates:
            contributions_known = False
        cumulative = running_contributions if contributions_known else None
        total_value = _consolidated_nav_near(
            date,
            nav_by_account,
            max_nav_distance_days,
        )
        change = None
        growth = None
        if cumulative is not None and total_value is not None:
            change = total_value - cumulative
            if abs(cumulative) > 1e-9:
                growth = (change / cumulative) * 100.0
        points.append(
            ContributionWealthPoint(
                date=date,
                cumulative_contributions_cad=cumulative,
                total_value_cad=total_value,
                change_in_value_cad=change,
                growth_pct=growth,
            )
        )
    return points


def get_value_vs_contributions(conn: sqlite3.Connection) -> List[ContributionWealthPoint]:
    return get_contribution_wealth_series(conn)


def _nav_by_account(
    conn: sqlite3.Connection,
) -> Dict[int, List[Tuple[dt.date, float]]]:
    grouped: Dict[int, List[Tuple[dt.date, float]]] = {}
    for row in daily_nav_series(conn):
        grouped.setdefault(int(row["account_id"]), []).append(
            (dt.date.fromisoformat(row["report_date"]), float(row["nav"]))
        )
    return grouped


def _consolidated_nav_near(
    date: str,
    nav_by_account: Dict[int, List[Tuple[dt.date, float]]],
    max_distance_days: int,
) -> Optional[float]:
    if not nav_by_account:
        return None

    target = dt.date.fromisoformat(date)
    total = 0.0
    included_accounts = 0
    for rows in nav_by_account.values():
        if not rows or target < rows[0][0]:
            continue
        candidates = [
            (nav_date, nav)
            for nav_date, nav in rows
            if abs((nav_date - target).days) <= max_distance_days
        ]
        if not candidates:
            return None
        _, nav = min(
            candidates,
            key=lambda item: (
                abs((item[0] - target).days),
                0 if item[0] >= target else 1,
                item[0],
            ),
        )
        total += nav
        included_accounts += 1

    return total if included_accounts else None
