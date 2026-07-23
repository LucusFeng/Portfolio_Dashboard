import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.repository.cash import latest_cash_balances, latest_cash_reconciliation_warnings
from app.repository.observations import latest_fx_rate
from app.repository.portfolio import contribution_cashflows
from app.services.portfolio import to_cad


@dataclass(frozen=True)
class CashAccountRow:
    account_label: str
    cad_cash: float
    usd_cash: float
    usd_cash_cad: Optional[float]
    total_cad: Optional[float]
    stale_reason: Optional[str]


@dataclass(frozen=True)
class CashReconciliationWarning:
    account_label: str
    currency: str
    check_type: str
    broker_value: float
    derived_value: float
    difference: float


@dataclass(frozen=True)
class CashData:
    accounts: List[CashAccountRow]
    cash_total_cad: float
    contributions_total_cad: float
    has_missing_fx: bool
    warnings: List[CashReconciliationWarning]


def get_cash(conn: sqlite3.Connection) -> CashData:
    usdcad = latest_fx_rate(conn)
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
        usd_cash_cad = to_cad(usd_cash, "USD", usdcad) if abs(usd_cash) > 1e-9 else 0.0
        stale_reason = None
        total_cad: Optional[float]
        if usd_cash_cad is None:
            total_cad = None
            stale_reason = "missing FX"
            has_missing_fx = True
        else:
            total_cad = cad_cash + usd_cash_cad
            cash_total_cad += total_cad
        account_rows.append(
            CashAccountRow(
                account_label=account_label,
                cad_cash=cad_cash,
                usd_cash=usd_cash,
                usd_cash_cad=usd_cash_cad,
                total_cad=total_cad,
                stale_reason=stale_reason,
            )
        )

    warnings = [
        CashReconciliationWarning(
            account_label=row["account_label"],
            currency=row["currency"],
            check_type=row["check_type"],
            broker_value=float(row["broker_value"]),
            derived_value=float(row["derived_value"]),
            difference=float(row["difference"]),
        )
        for row in latest_cash_reconciliation_warnings(conn)
    ]
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
        warnings=warnings,
    )
