from dataclasses import dataclass
from typing import List, Optional

import sqlite3

from app.repository.portfolio import open_lot_marks


@dataclass(frozen=True)
class LotPnlRow:
    account_label: str
    symbol: str
    name: str
    open_date: str
    remaining_qty: float
    cost_per_unit: float
    cost_basis: float
    price: Optional[float]
    market_value: Optional[float]
    unrealized_pnl: Optional[float]
    currency: str
    stale_reason: Optional[str]


def get_batch_pnl(conn: sqlite3.Connection) -> List[LotPnlRow]:
    rows: List[LotPnlRow] = []
    for row in open_lot_marks(conn):
        price = row["price"]
        stale_reason = None
        market_value = None
        pnl = None
        if price is None:
            stale_reason = "missing price"
        else:
            market_value = float(row["remaining_qty"]) * float(price)
            pnl = market_value - float(row["remaining_cost_basis"])
        rows.append(
            LotPnlRow(
                account_label=row["account_label"],
                symbol=row["symbol"],
                name=row["name"] or row["symbol"],
                open_date=row["open_date"],
                remaining_qty=float(row["remaining_qty"]),
                cost_per_unit=float(row["cost_per_unit"]),
                cost_basis=float(row["remaining_cost_basis"]),
                price=float(price) if price is not None else None,
                market_value=market_value,
                unrealized_pnl=pnl,
                currency=row["price_currency"] or row["cost_currency"],
                stale_reason=stale_reason,
            )
        )
    return rows
