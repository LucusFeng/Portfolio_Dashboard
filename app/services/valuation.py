from dataclasses import dataclass
from typing import List, Optional

import sqlite3

from app.services.batch_pnl import LotPnlRow, get_batch_pnl
from app.services.cash import CashData, get_cash
from app.services.growth import GrowthPoint, get_value_vs_contributions
from app.services.nav import get_consolidated_twr
from app.services.portfolio import HoldingRow, PortfolioData, get_portfolio


@dataclass(frozen=True)
class DashboardData(PortfolioData):
    batch_pnl: List[LotPnlRow]
    growth_points: List[GrowthPoint]
    cash: CashData
    positions_total_cad: float
    total_cad: float
    contributions_total_cad: float
    simple_return_pct: Optional[float]
    time_weighted_return_pct: Optional[float]


def build_dashboard_data(conn: sqlite3.Connection) -> DashboardData:
    portfolio = get_portfolio(conn)
    cash = get_cash(conn)
    total_cad = portfolio.grand_total_cad + cash.cash_total_cad
    simple_return_pct = None
    if abs(cash.contributions_total_cad) > 1e-9:
        simple_return_pct = ((total_cad - cash.contributions_total_cad) / cash.contributions_total_cad) * 100.0
    return DashboardData(
        holdings=portfolio.holdings,
        account_summaries=portfolio.account_summaries,
        consolidated=portfolio.consolidated,
        grand_total_cad=portfolio.grand_total_cad,
        latest_fx_rate=portfolio.latest_fx_rate,
        last_ingestion_message=portfolio.last_ingestion_message,
        reconciliation_warnings=portfolio.reconciliation_warnings,
        as_of_date=portfolio.as_of_date,
        has_mixed_snapshot_dates=portfolio.has_mixed_snapshot_dates,
        has_pending_cad_pnl=portfolio.has_pending_cad_pnl,
        batch_pnl=get_batch_pnl(conn),
        growth_points=get_value_vs_contributions(conn),
        cash=cash,
        positions_total_cad=portfolio.grand_total_cad,
        total_cad=total_cad,
        contributions_total_cad=cash.contributions_total_cad,
        simple_return_pct=simple_return_pct,
        time_weighted_return_pct=get_consolidated_twr(conn),
    )
