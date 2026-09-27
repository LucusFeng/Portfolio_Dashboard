import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.repository.cash import latest_cash_balances, latest_fx_rate_to_base
from app.repository.portfolio import contribution_cashflows_by_account
from app.services.portfolio import to_cad


@dataclass(frozen=True)
class CashAccountRow:
    account_id: int
    account_label: str
    cad_cash: float
    usd_cash: float
    net_cash_cad: Optional[float]
    contributions_total_cad: Optional[float]
    status: str


@dataclass(frozen=True)
class CashData:
    accounts: List[CashAccountRow]
    cash_total_cad: float
    contributions_total_cad: Optional[float]
    has_missing_fx: bool


def get_cash(conn: sqlite3.Connection) -> CashData:
    usdcad = latest_fx_rate_to_base(conn)
    accounts: Dict[int, Dict[str, object]] = {}

    for row in latest_cash_balances(conn):
        account = accounts.setdefault(
            int(row["account_id"]),
            {
                "label": row["account_label"],
                "CAD": 0.0,
                "USD": 0.0,
                "contributions": 0.0,
                "contributions_complete": True,
            },
        )
        account[row["currency"].upper()] = float(row["ending_cash"])

    for row in contribution_cashflows_by_account(conn):
        account = accounts.setdefault(
            int(row["account_id"]),
            {
                "label": row["account_label"],
                "CAD": 0.0,
                "USD": 0.0,
                "contributions": 0.0,
                "contributions_complete": True,
            },
        )
        amount_cad = to_cad(float(row["amount"]), row["currency"], usdcad)
        if amount_cad is None:
            account["contributions_complete"] = False
        else:
            account["contributions"] = float(account["contributions"]) + amount_cad

    account_rows: List[CashAccountRow] = []
    cash_total_cad = 0.0
    contributions_total_cad = 0.0
    has_missing_fx = False
    contributions_complete = True

    for account_id, values in sorted(
        accounts.items(),
        key=lambda item: str(item[1]["label"]),
    ):
        cad_cash = float(values.get("CAD", 0.0))
        usd_cash = float(values.get("USD", 0.0))
        status = "ok"
        if abs(usd_cash) > 1e-9 and usdcad is None:
            net_cash_cad = None
            status = "needs FX"
            has_missing_fx = True
        else:
            usd_cash_cad = (
                usd_cash * usdcad
                if abs(usd_cash) > 1e-9 and usdcad is not None
                else 0.0
            )
            net_cash_cad = cad_cash + usd_cash_cad
            cash_total_cad += net_cash_cad

        account_contributions_complete = bool(values["contributions_complete"])
        account_contributions = (
            float(values["contributions"])
            if account_contributions_complete
            else None
        )
        if account_contributions is None:
            contributions_complete = False
            has_missing_fx = True
            if status == "ok":
                status = "needs contribution FX"
        else:
            contributions_total_cad += account_contributions

        account_rows.append(
            CashAccountRow(
                account_id=account_id,
                account_label=str(values["label"]),
                cad_cash=cad_cash,
                usd_cash=usd_cash,
                net_cash_cad=net_cash_cad,
                contributions_total_cad=account_contributions,
                status=status,
            )
        )

    return CashData(
        accounts=account_rows,
        cash_total_cad=cash_total_cad,
        contributions_total_cad=(
            contributions_total_cad if contributions_complete else None
        ),
        has_missing_fx=has_missing_fx,
    )
