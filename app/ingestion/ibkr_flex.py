import datetime as dt
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from app.models import ParsedInstrument, ParsedPosition, ParsedTransaction
from app.repository.instruments import normalize_asset_class


SUPPORTED_ASSET_CLASSES = {"EQUITY", "ETF", "CASH"}


def _attr(node: ET.Element, *names: str) -> Optional[str]:
    lower_attrs = {key.lower(): value for key, value in node.attrib.items()}
    for name in names:
        value = lower_attrs.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def _float(value: Optional[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(str(value).replace(",", ""))


def _date(value: Optional[str]) -> str:
    if not value:
        return dt.date.today().isoformat()
    raw = value.strip()
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    if len(raw) >= 8 and raw[:8].isdigit():
        return "%s-%s-%s" % (raw[:4], raw[4:6], raw[6:8])
    return raw[:10]


def _account(node: ET.Element) -> Tuple[str, str]:
    account_id = _attr(node, "accountId", "account_id", "account") or "UNKNOWN"
    return account_id, _attr(node, "acctAlias", "accountAlias") or account_id


def _instrument_from_node(node: ET.Element) -> Optional[ParsedInstrument]:
    symbol = (_attr(node, "symbol", "ticker") or "").upper()
    currency = (_attr(node, "currency", "currencyPrimary") or "USD").upper()
    asset_class = normalize_asset_class(_attr(node, "assetCategory", "assetClass") or "")
    if not symbol or asset_class not in SUPPORTED_ASSET_CLASSES:
        return None
    return ParsedInstrument(
        asset_class=asset_class,
        symbol=symbol,
        name=_attr(node, "description", "name") or symbol,
        currency=currency,
        conid=_attr(node, "conid", "conId"),
        isin=_attr(node, "isin"),
    )


def parse_flex_positions(xml_text: str) -> List[ParsedPosition]:
    root = ET.fromstring(xml_text)
    positions: List[ParsedPosition] = []

    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in {"OpenPosition", "Position"}:
            continue
        instrument = _instrument_from_node(node)
        if instrument is None:
            continue
        quantity = _float(_attr(node, "position", "quantity", "qty"))
        if quantity is None:
            continue
        account_id, account_label = _account(node)
        positions.append(
            ParsedPosition(
                account_external_id=account_id,
                account_label=account_label,
                asset_class=instrument.asset_class,
                symbol=instrument.symbol,
                name=instrument.name,
                currency=instrument.currency,
                quantity=quantity,
                avg_cost=_float(_attr(node, "costBasisPrice", "avgCost", "averageCost")),
                conid=instrument.conid,
            )
        )

    for node in root.iter():
        if node.tag.split("}")[-1] != "CashReport":
            continue
        currency = (_attr(node, "currency") or "").upper()
        quantity = _float(_attr(node, "endingCash", "total", "cash", "settledCash"))
        if not currency or quantity is None:
            continue
        account_id, account_label = _account(node)
        positions.append(
            ParsedPosition(
                account_external_id=account_id,
                account_label=account_label,
                asset_class="CASH",
                symbol="CASH:%s" % currency,
                name="%s cash" % currency,
                currency=currency,
                quantity=quantity,
                avg_cost=1.0,
                conid=None,
            )
        )
    return positions


def parse_flex_transactions(xml_text: str, source: str = "ibkr_flex") -> List[ParsedTransaction]:
    root = ET.fromstring(xml_text)
    transactions: List[ParsedTransaction] = []

    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in {"Trade", "Execution"}:
            continue
        instrument = _instrument_from_node(node)
        if instrument is None:
            continue
        quantity = _float(_attr(node, "quantity", "qty", "shares"))
        price = _float(_attr(node, "tradePrice", "price", "trade_price"))
        if quantity is None or price is None:
            continue
        account_id, account_label = _account(node)
        side = (_attr(node, "buySell", "side", "transactionType") or "").upper()
        txn_type = "SELL" if side.startswith("S") or quantity < 0 else "BUY"
        abs_qty = abs(quantity)
        proceeds = _float(_attr(node, "proceeds", "netCash", "amount"))
        if proceeds is None:
            proceeds = abs_qty * price * (1 if txn_type == "SELL" else -1)
        transactions.append(
            ParsedTransaction(
                txn_date=_date(_attr(node, "tradeDate", "dateTime", "reportDate")),
                broker="IBKR",
                account_external_id=account_id,
                account_label=account_label,
                tax_type="UNKNOWN",
                txn_type=txn_type,
                quantity=abs_qty,
                price=price,
                amount=proceeds,
                currency=instrument.currency,
                source=source,
                external_id=_attr(node, "tradeID", "tradeId", "executionId", "ibExecID"),
                instrument=instrument,
            )
        )

    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in {"CashTransaction", "CashTransactions"}:
            continue
        amount = _float(_attr(node, "amount", "netCash", "proceeds"))
        currency = (_attr(node, "currency") or "").upper()
        if amount is None or not currency:
            continue
        account_id, account_label = _account(node)
        raw_type = (_attr(node, "type", "transactionType", "activityDescription") or "").upper()
        txn_type = _cash_type(raw_type, amount)
        transactions.append(
            ParsedTransaction(
                txn_date=_date(_attr(node, "dateTime", "date", "reportDate")),
                broker="IBKR",
                account_external_id=account_id,
                account_label=account_label,
                tax_type="UNKNOWN",
                txn_type=txn_type,
                amount=amount,
                currency=currency,
                source=source,
                external_id=_attr(node, "transactionID", "transactionId", "id"),
            )
        )
    return transactions


def _cash_type(raw_type: str, amount: float) -> str:
    if "DIV" in raw_type:
        return "DIVIDEND"
    if "FEE" in raw_type or "WITHHOLD" in raw_type or "TAX" in raw_type:
        return "FEE"
    if "WITHDRAW" in raw_type:
        return "WITHDRAWAL"
    if "DEPOSIT" in raw_type or "EFT" in raw_type or "TRANSFER" in raw_type:
        return "DEPOSIT" if amount >= 0 else "WITHDRAWAL"
    return "DEPOSIT" if amount >= 0 else "WITHDRAWAL"


def _open_url(url: str, timeout: int = 30) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _query_url(base_url: str, endpoint: str, params: Dict[str, str]) -> str:
    return "%s/%s?%s" % (
        base_url.rstrip("/"),
        endpoint,
        urllib.parse.urlencode(params),
    )


class FlexClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def fetch_statement(self, token: str, query_id: str, max_attempts: int = 10) -> str:
        request_url = _query_url(
            self.base_url,
            "FlexStatementService.SendRequest",
            {"t": token, "q": query_id, "v": "3"},
        )
        request_xml = _open_url(request_url)
        reference_code = self._reference_code(request_xml)

        for _ in range(max_attempts):
            statement_url = _query_url(
                self.base_url,
                "FlexStatementService.GetStatement",
                {"t": token, "q": reference_code, "v": "3"},
            )
            statement_xml = _open_url(statement_url)
            if "Statement generation in progress" not in statement_xml:
                return statement_xml
            time.sleep(3)
        raise RuntimeError("IBKR Flex statement was not ready after polling.")

    @staticmethod
    def _reference_code(xml_text: str) -> str:
        root = ET.fromstring(xml_text)
        for node in root.iter():
            if node.tag.split("}")[-1] == "ReferenceCode" and node.text:
                return node.text.strip()
        raise RuntimeError("IBKR Flex response did not include a ReferenceCode.")


def today_snapshot_date() -> str:
    return dt.date.today().isoformat()
