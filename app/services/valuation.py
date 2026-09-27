from dataclasses import dataclass, replace
from typing import List, Optional

import sqlite3

from app.services.batch_pnl import LotPnlRow, get_batch_pnl
from app.services.cash import CashData, get_cash
from app.services.growth import ContributionWealthPoint, get_contribution_wealth_series
from app.services.nav import get_consolidated_twr
from app.services.portfolio import AccountSummary, HoldingRow, PortfolioData, get_portfolio


@dataclass(frozen=True)
class DashboardData(PortfolioData):
    batch_pnl: List[LotPnlRow]
    contribution_wealth_points: List[ContributionWealthPoint]
    cash: CashData
    positions_total_cad: float
    total_cad: float
    contributions_total_cad: Optional[float]
    simple_return_pct: Optional[float]
    time_weighted_return_pct: Optional[float]


def build_dashboard_data(conn: sqlite3.Connection) -> DashboardData:
    portfolio = get_portfolio(conn)
    cash = get_cash(conn)
    account_summaries = _with_account_returns(
        portfolio.account_summaries,
        cash,
    )
    total_cad = portfolio.grand_total_cad + cash.cash_total_cad
    contributions_total_cad = cash.contributions_total_cad
    simple_return_pct = None
    positions_complete = all(
        summary.missing_prices == 0 for summary in account_summaries
    )
    if (
        contributions_total_cad is not None
        and abs(contributions_total_cad) > 1e-9
        and not cash.has_missing_fx
        and positions_complete
    ):
        simple_return_pct = (
            (total_cad - contributions_total_cad)
            / contributions_total_cad
        ) * 100.0

    return DashboardData(
        holdings=portfolio.holdings,
        account_summaries=account_summaries,
        consolidated=portfolio.consolidated,
        grand_total_cad=portfolio.grand_total_cad,
        latest_fx_rate=portfolio.latest_fx_rate,
        last_ingestion_message=portfolio.last_ingestion_message,
        reconciliation_warnings=portfolio.reconciliation_warnings,
        as_of_date=portfolio.as_of_date,
        has_mixed_snapshot_dates=portfolio.has_mixed_snapshot_dates,
        has_pending_cad_pnl=portfolio.has_pending_cad_pnl,
        batch_pnl=get_batch_pnl(conn),
        contribution_wealth_points=get_contribution_wealth_series(conn),
        cash=cash,
        positions_total_cad=portfolio.grand_total_cad,
        total_cad=total_cad,
        contributions_total_cad=contributions_total_cad,
        simple_return_pct=simple_return_pct,
        time_weighted_return_pct=get_consolidated_twr(conn),
    )


def _with_account_returns(
    summaries: List[AccountSummary],
    cash: CashData,
) -> List[AccountSummary]:
    cash_by_account = {row.account_id: row for row in cash.accounts}
    enriched: List[AccountSummary] = []
    seen = set()

    for summary in summaries:
        cash_row = cash_by_account.get(summary.account_id)
        cash_value = cash_row.net_cash_cad if cash_row is not None else 0.0
        contributions = (
            cash_row.contributions_total_cad
            if cash_row is not None
            else 0.0
        )
        total_value = None
        simple_return = None
        if summary.missing_prices == 0 and cash_value is not None:
            total_value = summary.market_value_cad + cash_value
            if contributions is not None and abs(contributions) > 1e-9:
                simple_return = (
                    (total_value - contributions) / contributions
                ) * 100.0
        enriched.append(
            replace(
                summary,
                cash_value_cad=cash_value,
                total_value_cad=total_value,
                contributions_total_cad=contributions,
                simple_return_pct=simple_return,
            )
        )
        seen.add(summary.account_id)

    for cash_row in cash.accounts:
        if cash_row.account_id in seen:
            continue
        total_value = cash_row.net_cash_cad
        simple_return = None
        if (
            total_value is not None
            and cash_row.contributions_total_cad is not None
            and abs(cash_row.contributions_total_cad) > 1e-9
        ):
            simple_return = (
                (total_value - cash_row.contributions_total_cad)
                / cash_row.contributions_total_cad
            ) * 100.0
        enriched.append(
            AccountSummary(
                account_id=cash_row.account_id,
                account_label=cash_row.account_label,
                market_value_cad=0.0,
                missing_prices=0,
                cash_value_cad=cash_row.net_cash_cad,
                total_value_cad=total_value,
                contributions_total_cad=cash_row.contributions_total_cad,
                simple_return_pct=simple_return,
            )
        )

    return sorted(enriched, key=lambda summary: summary.account_label)
