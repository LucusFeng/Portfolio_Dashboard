import datetime as dt
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


SUPPORTED_ASSET_CLASSES = {"STK", "ETF", "CASH"}


@dataclass(frozen=True)
class ParsedPosition:
    account_external_id: str
    account_label: str
    asset_class: str
    symbol: str
    name: str
    currency: str
    quantity: float
    avg_cost: Optional[float]
    conid: Optional[str]


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


def _asset_class(value: Optional[str], symbol: str) -> str:
    if symbol.upper().startswith("CASH:"):
        return "CASH"
    normalized = (value or "").upper()
    if normalized in {"STK", "STOCK", "COMMON STOCK"}:
        return "STK"
    if normalized in {"ETF", "EXCHANGE TRADED FUND"}:
        return "ETF"
    if normalized in {"CASH", "CASHREPORT"}:
        return "CASH"
    return normalized or "UNKNOWN"


def parse_flex_xml(xml_text: str) -> List[ParsedPosition]:
    root = ET.fromstring(xml_text)
    positions: List[ParsedPosition] = []

    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in {"OpenPosition", "Position"}:
            continue
        account_id = _attr(node, "accountId", "account_id", "account") or "UNKNOWN"
        symbol = _attr(node, "symbol", "ticker") or ""
        currency = (_attr(node, "currency", "currencyPrimary") or "USD").upper()
        asset_class = _asset_class(_attr(node, "assetCategory", "assetClass"), symbol)
        if asset_class not in SUPPORTED_ASSET_CLASSES:
            continue
        quantity = _float(_attr(node, "position", "quantity", "qty"))
        if quantity is None:
            continue
        avg_cost = _float(_attr(node, "costBasisPrice", "avgCost", "averageCost"))
        positions.append(
            ParsedPosition(
                account_external_id=account_id,
                account_label=_attr(node, "acctAlias", "accountAlias") or account_id,
                asset_class=asset_class,
                symbol=symbol.upper(),
                name=_attr(node, "description", "name") or symbol.upper(),
                currency=currency,
                quantity=quantity,
                avg_cost=avg_cost,
                conid=_attr(node, "conid", "conId"),
            )
        )

    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag != "CashReport":
            continue
        currency = (_attr(node, "currency") or "").upper()
        if not currency:
            continue
        quantity = _float(_attr(node, "endingCash", "total", "cash", "settledCash"))
        if quantity is None:
            continue
        account_id = _attr(node, "accountId", "account") or "UNKNOWN"
        symbol = "CASH:%s" % currency
        positions.append(
            ParsedPosition(
                account_external_id=account_id,
                account_label=_attr(node, "acctAlias", "accountAlias") or account_id,
                asset_class="CASH",
                symbol=symbol,
                name="%s cash" % currency,
                currency=currency,
                quantity=quantity,
                avg_cost=1.0,
                conid=None,
            )
        )

    return positions


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
