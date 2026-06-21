import csv
from io import StringIO
from typing import Dict, List, Optional

from app.models import ParsedInstrument, ParsedTransaction


def _pick(row: Dict[str, str], *names: str) -> Optional[str]:
    normalized = {
        key.strip().lower(): (value or "").strip()
        for key, value in row.items()
        if key is not None
    }
    for name in names:
        value = normalized.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def _float(value: Optional[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(str(value).replace("$", "").replace(",", "").strip())


def _type(raw: str, amount: float) -> str:
    value = raw.upper()
    if "BUY" in value:
        return "BUY"
    if "SELL" in value:
        return "SELL"
    if "DIV" in value:
        return "DIVIDEND"
    if "FEE" in value:
        return "FEE"
    if "WITHDRAW" in value:
        return "WITHDRAWAL"
    if "DEPOSIT" in value or "CONTRIBUTION" in value:
        return "DEPOSIT"
    return "DEPOSIT" if amount >= 0 else "WITHDRAWAL"


def parse_cibc_transactions(csv_text: str, account_label: str = "TFSA") -> List[ParsedTransaction]:
    reader = csv.DictReader(StringIO(csv_text))
    transactions: List[ParsedTransaction] = []
    for index, row in enumerate(reader, start=1):
        date = _pick(row, "date", "transaction date", "trade date", "settlement date")
        raw_type = _pick(row, "type", "transaction type", "activity", "description") or ""
        amount = _float(_pick(row, "amount", "net amount", "net cash", "total"))
        currency = (_pick(row, "currency", "ccy") or "CAD").upper()
        if not date or amount is None:
            continue
        symbol = (_pick(row, "symbol", "ticker", "security symbol") or "").upper()
        quantity = _float(_pick(row, "quantity", "qty", "shares"))
        price = _float(_pick(row, "price", "trade price"))
        txn_type = _type(raw_type, amount)
        instrument = None
        if symbol:
            instrument = ParsedInstrument(
                asset_class="ETF" if symbol.endswith(".TO") else "EQUITY",
                symbol=symbol,
                name=_pick(row, "security", "security name", "description") or symbol,
                currency=currency,
            )
        transactions.append(
            ParsedTransaction(
                txn_date=date[:10],
                broker="CIBC",
                account_external_id=account_label,
                account_label=account_label,
                tax_type="TFSA",
                txn_type=txn_type,
                quantity=abs(quantity) if quantity is not None else None,
                price=price,
                amount=amount,
                currency=currency,
                source="cibc_csv",
                external_id=_pick(row, "id", "transaction id", "reference") or "cibc-%s-%s" % (date, index),
                instrument=instrument,
            )
        )
    return transactions
