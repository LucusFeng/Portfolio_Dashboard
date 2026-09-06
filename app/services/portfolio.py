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
    derived_quantity: Optional[float]
    avg_cost: Optional[float]
    cost_basis: Optional[float]
    price: Optional[float]
    market_value: Optional[float]
    market_value_cad: Optional[float]
    unrealized_pnl: Optional[float]
    unrealized_pnl_cad: Optional[float]
    value_source: str
    stale_reason: Optional[str]
    snapshot_date: Optional[str] = None
    statement_generated_at: Optional[str] = None
    ingested_at: Optional[str] = None
    cad_pnl_status: Optional[str] = None
    pnl_message: Optional[str] = None


@dataclass(frozen=True)
class AccountSummary:
    account_label: str
    market_value_cad: float
    missing_prices: int
    snapshot_date: Optional[str] = None
    statement_generated_at: Optional[str] = None
    ingested_at: Optional[str] = None
    cad_pnl_pending: int = 0


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
    as_of_date: Optional[str]
    has_mixed_snapshot_dates: bool
    has_pending_cad_pnl: bool


def to_cad(value: float, currency: str, usdcad: Optional[float]) -> Optional[float]:
    if currency.upper() == "CAD":
        return value
    if currency.upper() == "USD" and usdcad is not None:
        return value * usdcad
    return None


def _holding(row: sqlite3.Row, usdcad: Optional[float]) -> HoldingRow:
    flex_value_base = row["flex_value_base"]
    flex_value_native = row["flex_value_native"]
    has_flex_value = flex_value_base is not None and flex_value_native is not None
    currency = row["flex_native_currency"] or row["price_currency"] or row["instrument_currency"]
    price = row["price"]

    market_value = None
    market_value_cad = None
    unrealized_pnl = None
    unrealized_pnl_cad = None
    stale_reason = None
    cost_basis = float(row["cost_basis"]) if row["cost_basis"] is not None else None
    pnl_ready = row["pnl_ready"]
    cad_pnl_status = None
    pnl_message = row["pnl_message"] or row["pnl_warning"]

    if has_flex_value:
        market_value = float(flex_value_native)
        market_value_cad = float(flex_value_base)
        unrealized_pnl_cad = (
            float(row["flex_unrealized_pnl_cad"]) if row["flex_unrealized_pnl_cad"] is not None else None
        )
        if unrealized_pnl_cad is None:
            cad_pnl_status = "pending" if pnl_ready == 0 else "missing"
        else:
            cad_pnl_status = "ready"
        if cost_basis is not None:
            unrealized_pnl = market_value - cost_basis
    elif row["account_broker"] == "IBKR":
        stale_reason = "missing Flex value"
    elif price is None:
        stale_reason = "missing price"
    else:
        market_value = float(row["quantity"]) * float(price)
        market_value_cad = to_cad(market_value, currency, usdcad)
        if market_value_cad is None:
            stale_reason = "missing FX"
        if row["avg_cost"] is not None:
            unrealized_pnl = market_value - (float(row["quantity"]) * float(row["avg_cost"]))
            unrealized_pnl_cad = to_cad(unrealized_pnl, currency, usdcad)

    if market_value is not None and cost_basis is None:
        stale_reason = "missing cost basis"

    return HoldingRow(
        account_label=row["account_label"],
        symbol=row["symbol"],
        name=row["name"] or row["symbol"],
        asset_class=row["asset_class"],
        currency=currency,
        quantity=float(row["quantity"]),
        derived_quantity=float(row["derived_quantity"]) if row["derived_quantity"] is not None else None,
        avg_cost=float(row["avg_cost"]) if row["avg_cost"] is not None else None,
        cost_basis=cost_basis,
        price=float(price) if price is not None else None,
        market_value=market_value,
        market_value_cad=market_value_cad,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_cad=unrealized_pnl_cad,
        value_source=row["value_source"],
        stale_reason=stale_reason,
        snapshot_date=row["snapshot_date"],
        statement_generated_at=row["statement_generated_at"],
        ingested_at=row["ingested_at"],
        cad_pnl_status=cad_pnl_status,
        pnl_message=pnl_message,
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
                "unrealized_pnl_cad": 0.0,
                "missing": False,
                "dates": set(),
                "cad_pnl_pending": False,
                "cad_pnl_missing": False,
            },
        )
        bucket["quantity"] = float(bucket["quantity"]) + holding.quantity
        if holding.snapshot_date:
            bucket["dates"].add(holding.snapshot_date)
        if holding.market_value is None or holding.market_value_cad is None:
            bucket["missing"] = True
        else:
            bucket["market_value"] = float(bucket["market_value"]) + holding.market_value
            bucket["market_value_cad"] = float(bucket["market_value_cad"]) + holding.market_value_cad
        if holding.unrealized_pnl is not None:
            bucket["unrealized_pnl"] = float(bucket["unrealized_pnl"]) + holding.unrealized_pnl
        if holding.cad_pnl_status == "pending":
            bucket["cad_pnl_pending"] = True
        elif holding.unrealized_pnl_cad is not None:
            bucket["unrealized_pnl_cad"] = float(bucket["unrealized_pnl_cad"]) + holding.unrealized_pnl_cad
        elif holding.value_source == "IBKR Flex":
            bucket["cad_pnl_missing"] = True

    rows = []
    for bucket in grouped.values():
        sample = bucket["sample"]
        assert isinstance(sample, HoldingRow)
        missing = bool(bucket["missing"])
        cad_pnl_pending = bool(bucket["cad_pnl_pending"])
        cad_pnl_missing = bool(bucket["cad_pnl_missing"])
        cad_pnl_status = "pending" if cad_pnl_pending else "missing" if cad_pnl_missing else "ready"
        snapshot_dates = sorted(bucket["dates"])
        rows.append(
            HoldingRow(
                account_label="All accounts",
                symbol=sample.symbol,
                name=sample.name,
                asset_class=sample.asset_class,
                currency=sample.currency,
                quantity=float(bucket["quantity"]),
                derived_quantity=None,
                avg_cost=None,
                cost_basis=None,
                price=sample.price,
                market_value=None if missing else float(bucket["market_value"]),
                market_value_cad=None if missing else float(bucket["market_value_cad"]),
                unrealized_pnl=float(bucket["unrealized_pnl"]),
                unrealized_pnl_cad=None if cad_pnl_pending or cad_pnl_missing else float(bucket["unrealized_pnl_cad"]),
                value_source="Mixed",
                stale_reason="incomplete marks" if missing else None,
                snapshot_date=", ".join(snapshot_dates) if snapshot_dates else None,
                cad_pnl_status=cad_pnl_status,
            )
        )
    return sorted(rows, key=lambda item: item.symbol)


def get_portfolio(conn: sqlite3.Connection) -> PortfolioData:
    usdcad = latest_fx_rate(conn)
    holdings = [_holding(row, usdcad) for row in latest_position_marks(conn)]
    accounts: Dict[str, Dict[str, object]] = {}
    for holding in holdings:
        bucket = accounts.setdefault(
            holding.account_label,
            {
                "market_value_cad": 0.0,
                "missing_prices": 0.0,
                "snapshot_dates": set(),
                "statement_generated_values": [],
                "ingested_values": [],
                "cad_pnl_pending": 0,
            },
        )
        if holding.market_value_cad is None:
            bucket["missing_prices"] = float(bucket["missing_prices"]) + 1
        else:
            bucket["market_value_cad"] = float(bucket["market_value_cad"]) + holding.market_value_cad
        if holding.snapshot_date:
            bucket["snapshot_dates"].add(holding.snapshot_date)
        if holding.statement_generated_at:
            bucket["statement_generated_values"].append(holding.statement_generated_at)
        if holding.ingested_at:
            bucket["ingested_values"].append(holding.ingested_at)
        if holding.cad_pnl_status == "pending":
            bucket["cad_pnl_pending"] = int(bucket["cad_pnl_pending"]) + 1
    summaries = [
        AccountSummary(
            label,
            float(values["market_value_cad"]),
            int(values["missing_prices"]),
            max(values["snapshot_dates"]) if values["snapshot_dates"] else None,
            max(values["statement_generated_values"]) if values["statement_generated_values"] else None,
            max(values["ingested_values"]) if values["ingested_values"] else None,
            int(values["cad_pnl_pending"]),
        )
        for label, values in sorted(accounts.items())
    ]
    snapshot_dates = sorted({summary.snapshot_date for summary in summaries if summary.snapshot_date})
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
        as_of_date=snapshot_dates[-1] if snapshot_dates else None,
        has_mixed_snapshot_dates=len(snapshot_dates) > 1,
        has_pending_cad_pnl=any(summary.cad_pnl_pending > 0 for summary in summaries),
    )
