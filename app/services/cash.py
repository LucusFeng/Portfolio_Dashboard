import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.repository.cash import latest_cash_balances, latest_fx_rate_to_base
from app.repository.portfolio import contribution_cashflows
from app.services.portfolio import to_cad


@dataclass(frozen=True)
class CashAccountRow:
    account_label: str
    cad_cash: float
    usd_cash: float
    net_cash_cad: Optional[float]
    status: str


@dataclass(frozen=True)
class CashData:
    accounts: List[CashAccountRow]
    cash_total_cad: float
    contributions_total_cad: float
    has_missing_fx: bool


def get_cash(conn: sqlite3.Connection) -> CashData:
    usdcad = latest_fx_rate_to_base(conn)
    balances: Dict[str, Dict[str, float]] = {}
    for row in latest_cash_balances(conn):
        account = balances.setdefault(row["account_label"], {"CAD": 0.0, "USD": 0.0})
        account[row["currency"].upper()] = float(row["ending_cash"])

    account_rows: List[CashAccountRow] = []
    cash_total_cad = 0.0
    has_missing_fx = False
    for account_label, currencies in sorted(balances.items()):
        cad_cash = currencies.get("CAD", 0.0)
        usd_cash = currencies.get("USD", 0.0)
        net_cash_cad: Optional[float]
        status = "ok"
        if abs(usd_cash) > 1e-9 and usdcad is None:
            net_cash_cad = None
            status = "needs FX"
            has_missing_fx = True
        else:
            usd_cash_cad = usd_cash * usdcad if abs(usd_cash) > 1e-9 and usdcad is not None else 0.0
            net_cash_cad = cad_cash + usd_cash_cad
            cash_total_cad += net_cash_cad
        account_rows.append(
            CashAccountRow(
                account_label=account_label,
                cad_cash=cad_cash,
                usd_cash=usd_cash,
                net_cash_cad=net_cash_cad,
                status=status,
            )
        )

    contributions_total_cad = 0.0
    for row in contribution_cashflows(conn):
        amount_cad = to_cad(float(row["amount"]), row["currency"], usdcad)
        if amount_cad is not None:
            contributions_total_cad += amount_cad

    return CashData(
        accounts=account_rows,
        cash_total_cad=cash_total_cad,
        contributions_total_cad=contributions_total_cad,
        has_missing_fx=has_missing_fx,
    )
