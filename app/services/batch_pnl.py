from dataclasses import dataclass
from typing import List, Optional

import sqlite3

from app.repository.observations import latest_fx_rate
from app.repository.portfolio import open_lot_marks
from app.services.portfolio import to_cad


@dataclass(frozen=True)
class LotPnlRow:
    account_label: str
    symbol: str
    name: str
    open_date: str
    remaining_qty: float
    cost_per_unit: float
    price: Optional[float]
    market_value_cad: Optional[float]
    unrealized_pnl_cad: Optional[float]
    stale_reason: Optional[str]


def get_batch_pnl(conn: sqlite3.Connection) -> List[LotPnlRow]:
    usdcad = latest_fx_rate(conn)
    rows: List[LotPnlRow] = []
    for row in open_lot_marks(conn):
        price = row["price"]
        stale_reason = None
        market_value_cad = None
        pnl_cad = None
        if price is None:
            stale_reason = "missing price"
        else:
            market_value = float(row["remaining_qty"]) * float(price)
            market_value_cad = to_cad(market_value, row["price_currency"] or row["cost_currency"], usdcad)
            cost_value_cad = to_cad(
                float(row["remaining_qty"]) * float(row["cost_per_unit"]),
                row["cost_currency"],
                usdcad,
            )
            if market_value_cad is None or cost_value_cad is None:
                stale_reason = "missing FX"
            else:
                pnl_cad = market_value_cad - cost_value_cad
        rows.append(
            LotPnlRow(
                account_label=row["account_label"],
                symbol=row["symbol"],
                name=row["name"] or row["symbol"],
                open_date=row["open_date"],
                remaining_qty=float(row["remaining_qty"]),
                cost_per_unit=float(row["cost_per_unit"]),
                price=float(price) if price is not None else None,
                market_value_cad=market_value_cad,
                unrealized_pnl_cad=pnl_cad,
                stale_reason=stale_reason,
            )
        )
    return rows
