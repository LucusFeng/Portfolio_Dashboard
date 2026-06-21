from dataclasses import dataclass
from typing import List

import sqlite3

from app.services.batch_pnl import LotPnlRow, get_batch_pnl
from app.services.growth import GrowthPoint, get_value_vs_contributions
from app.services.portfolio import HoldingRow, PortfolioData, get_portfolio


@dataclass(frozen=True)
class DashboardData(PortfolioData):
    batch_pnl: List[LotPnlRow]
    growth_points: List[GrowthPoint]


def build_dashboard_data(conn: sqlite3.Connection) -> DashboardData:
    portfolio = get_portfolio(conn)
    return DashboardData(
        holdings=portfolio.holdings,
        account_summaries=portfolio.account_summaries,
        consolidated=portfolio.consolidated,
        grand_total_cad=portfolio.grand_total_cad,
        latest_fx_rate=portfolio.latest_fx_rate,
        last_ingestion_message=portfolio.last_ingestion_message,
        reconciliation_warnings=portfolio.reconciliation_warnings,
        batch_pnl=get_batch_pnl(conn),
        growth_points=get_value_vs_contributions(conn),
    )
