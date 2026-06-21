from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import sqlite3

from app.repository.observations import latest_fx_rate
from app.repository.portfolio import latest_position_marks, latest_reconciliation_warnings
from app.repository.runs import latest_run_message


@dataclass(frozen=True)
class HoldingRow:
    account_label: str
    symbol: str
    name: str
    asset_class: str
    currency: str
    quantity: float
    avg_cost: Optional[float]
    price: Optional[float]
    market_value: Optional[float]
    market_value_cad: Optional[float]
    unrealized_pnl: Optional[float]
    stale_reason: Optional[str]


@dataclass(frozen=True)
class AccountSummary:
    account_label: str
    market_value_cad: float
    missing_prices: int


@dataclass(frozen=True)
class ReconciliationWarning:
    account_label: str
    symbol: str
    broker_quantity: float
    derived_quantity: float
    difference: float


@dataclass(frozen=True)
class PortfolioData:
    holdings: List[HoldingRow]
    account_summaries: List[AccountSummary]
    consolidated: List[HoldingRow]
    grand_total_cad: float
    latest_fx_rate: Optional[float]
    last_ingestion_message: Optional[str]
    reconciliation_warnings: List[ReconciliationWarning]


def to_cad(value: float, currency: str, usdcad: Optional[float]) -> Optional[float]:
    if currency.upper() == "CAD":
        return value
    if currency.upper() == "USD" and usdcad is not None:
        return value * usdcad
    return None


def _holding(row: sqlite3.Row, usdcad: Optional[float]) -> HoldingRow:
    currency = row["price_currency"] or row["instrument_currency"]
    price = row["price"]
    if row["asset_class"] == "CASH" and price is None:
        price = 1.0
        currency = row["instrument_currency"]

    market_value = None
    market_value_cad = None
    unrealized_pnl = None
    stale_reason = None
    if price is None:
        stale_reason = "missing price"
    else:
        market_value = float(row["quantity"]) * float(price)
        market_value_cad = to_cad(market_value, currency, usdcad)
        if market_value_cad is None:
            stale_reason = "missing FX"
        if row["avg_cost"] is not None:
            unrealized_pnl = (float(price) - float(row["avg_cost"])) * float(row["quantity"])

    return HoldingRow(
        account_label=row["account_label"],
        symbol=row["symbol"],
        name=row["name"] or row["symbol"],
        asset_class=row["asset_class"],
        currency=currency,
        quantity=float(row["quantity"]),
        avg_cost=float(row["avg_cost"]) if row["avg_cost"] is not None else None,
        price=float(price) if price is not None else None,
        market_value=market_value,
        market_value_cad=market_value_cad,
        unrealized_pnl=unrealized_pnl,
        stale_reason=stale_reason,
    )


def _consolidate(holdings: List[HoldingRow]) -> List[HoldingRow]:
    grouped: Dict[Tuple[str, str], Dict[str, object]] = {}
    for holding in holdings:
        key = (holding.symbol, holding.currency)
        bucket = grouped.setdefault(
            key,
            {
                "sample": holding,
                "quantity": 0.0,
                "market_value": 0.0,
                "market_value_cad": 0.0,
                "unrealized_pnl": 0.0,
                "missing": False,
            },
        )
        bucket["quantity"] = float(bucket["quantity"]) + holding.quantity
        if holding.market_value is None or holding.market_value_cad is None:
            bucket["missing"] = True
        else:
            bucket["market_value"] = float(bucket["market_value"]) + holding.market_value
            bucket["market_value_cad"] = float(bucket["market_value_cad"]) + holding.market_value_cad
        if holding.unrealized_pnl is not None:
            bucket["unrealized_pnl"] = float(bucket["unrealized_pnl"]) + holding.unrealized_pnl

    rows = []
    for bucket in grouped.values():
        sample = bucket["sample"]
        assert isinstance(sample, HoldingRow)
        missing = bool(bucket["missing"])
        rows.append(
            HoldingRow(
                account_label="All accounts",
                symbol=sample.symbol,
                name=sample.name,
                asset_class=sample.asset_class,
                currency=sample.currency,
                quantity=float(bucket["quantity"]),
                avg_cost=None,
                price=sample.price,
                market_value=None if missing else float(bucket["market_value"]),
                market_value_cad=None if missing else float(bucket["market_value_cad"]),
                unrealized_pnl=float(bucket["unrealized_pnl"]),
                stale_reason="incomplete marks" if missing else None,
            )
        )
    return sorted(rows, key=lambda item: item.symbol)


def get_portfolio(conn: sqlite3.Connection) -> PortfolioData:
    usdcad = latest_fx_rate(conn)
    holdings = [_holding(row, usdcad) for row in latest_position_marks(conn)]
    accounts: Dict[str, Dict[str, float]] = {}
    for holding in holdings:
        bucket = accounts.setdefault(holding.account_label, {"market_value_cad": 0.0, "missing_prices": 0.0})
        if holding.market_value_cad is None:
            bucket["missing_prices"] += 1
        else:
            bucket["market_value_cad"] += holding.market_value_cad
    summaries = [
        AccountSummary(label, float(values["market_value_cad"]), int(values["missing_prices"]))
        for label, values in sorted(accounts.items())
    ]
    warnings = [
        ReconciliationWarning(
            row["account_label"],
            row["symbol"],
            float(row["broker_quantity"]),
            float(row["derived_quantity"]),
            float(row["difference"]),
        )
        for row in latest_reconciliation_warnings(conn)
    ]
    return PortfolioData(
        holdings=holdings,
        account_summaries=summaries,
        consolidated=_consolidate(holdings),
        grand_total_cad=sum(summary.market_value_cad for summary in summaries),
        latest_fx_rate=usdcad,
        last_ingestion_message=latest_run_message(conn),
        reconciliation_warnings=warnings,
    )
