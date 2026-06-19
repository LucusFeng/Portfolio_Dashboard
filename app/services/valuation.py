import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


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
class DashboardData:
    holdings: List[HoldingRow]
    account_summaries: List[AccountSummary]
    consolidated: List[HoldingRow]
    grand_total_cad: float
    latest_fx_rate: Optional[float]
    last_ingestion_message: Optional[str]


LATEST_POSITIONS_SQL = """
WITH latest_date AS (
    SELECT account_id, instrument_id, MAX(snapshot_date) AS snapshot_date
    FROM position_snapshots
    GROUP BY account_id, instrument_id
),
latest_id AS (
    SELECT p.account_id, p.instrument_id, MAX(p.id) AS id
    FROM position_snapshots p
    JOIN latest_date d
      ON d.account_id = p.account_id
     AND d.instrument_id = p.instrument_id
     AND d.snapshot_date = p.snapshot_date
    GROUP BY p.account_id, p.instrument_id
),
latest_price_id AS (
    SELECT instrument_id, MAX(id) AS id
    FROM prices
    GROUP BY instrument_id
)
SELECT
    a.label AS account_label,
    i.id AS instrument_id,
    i.symbol,
    i.name,
    i.asset_class,
    i.currency AS instrument_currency,
    i.conid,
    p.quantity,
    p.avg_cost,
    pr.price,
    pr.currency AS price_currency
FROM position_snapshots p
JOIN latest_id lp ON lp.id = p.id
JOIN accounts a ON a.id = p.account_id
JOIN instruments i ON i.id = p.instrument_id
LEFT JOIN latest_price_id lpr ON lpr.instrument_id = i.id
LEFT JOIN prices pr ON pr.id = lpr.id
ORDER BY a.label, i.symbol
"""


def _latest_fx_rate(conn: sqlite3.Connection, pair: str = "USDCAD") -> Optional[float]:
    row = conn.execute(
        "SELECT rate FROM fx_rates WHERE pair = ? ORDER BY id DESC LIMIT 1",
        (pair,),
    ).fetchone()
    return float(row["rate"]) if row else None


def _to_cad(value: float, currency: str, usdcad: Optional[float]) -> Optional[float]:
    if currency.upper() == "CAD":
        return value
    if currency.upper() == "USD" and usdcad is not None:
        return value * usdcad
    return None


def _holding_from_row(row: sqlite3.Row, usdcad: Optional[float]) -> HoldingRow:
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
        market_value_cad = _to_cad(market_value, currency, usdcad)
        if market_value_cad is None:
            stale_reason = "missing FX"
        avg_cost = row["avg_cost"]
        if avg_cost is not None:
            unrealized_pnl = (float(price) - float(avg_cost)) * float(row["quantity"])

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

    consolidated = []
    for bucket in grouped.values():
        sample = bucket["sample"]
        assert isinstance(sample, HoldingRow)
        missing = bool(bucket["missing"])
        consolidated.append(
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
    return sorted(consolidated, key=lambda item: item.symbol)


def build_dashboard_data(conn: sqlite3.Connection) -> DashboardData:
    usdcad = _latest_fx_rate(conn)
    holdings = [_holding_from_row(row, usdcad) for row in conn.execute(LATEST_POSITIONS_SQL)]
    account_buckets: Dict[str, Dict[str, float]] = {}
    for holding in holdings:
        bucket = account_buckets.setdefault(
            holding.account_label,
            {"market_value_cad": 0.0, "missing_prices": 0.0},
        )
        if holding.market_value_cad is None:
            bucket["missing_prices"] += 1
        else:
            bucket["market_value_cad"] += holding.market_value_cad

    summaries = [
        AccountSummary(
            account_label=label,
            market_value_cad=float(values["market_value_cad"]),
            missing_prices=int(values["missing_prices"]),
        )
        for label, values in sorted(account_buckets.items())
    ]
    message_row = conn.execute(
        "SELECT kind || ': ' || status || COALESCE(' - ' || message, '') AS message FROM ingestion_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return DashboardData(
        holdings=holdings,
        account_summaries=summaries,
        consolidated=_consolidate(holdings),
        grand_total_cad=sum(summary.market_value_cad for summary in summaries),
        latest_fx_rate=usdcad,
        last_ingestion_message=message_row["message"] if message_row else None,
    )
