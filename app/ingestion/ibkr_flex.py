import datetime as dt
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from app.models import ParsedCashReport, ParsedInstrument, ParsedPosition, ParsedPositionValue, ParsedTransaction
from app.repository.instruments import normalize_asset_class


SUPPORTED_ASSET_CLASSES = {"EQUITY", "ETF"}


def summarize_flex_xml(xml_text: str) -> Dict[str, int]:
    root = ET.fromstring(xml_text)
    counts = {
        "Trade": 0,
        "Execution": 0,
        "CashTransaction": 0,
        "CashTransactions": 0,
        "OpenPosition": 0,
        "Position": 0,
        "CashReport": 0,
        "CashReportCurrency": 0,
    }
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag in counts:
            counts[tag] += 1
    return counts


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
    raw = value.strip().split(";", 1)[0]
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

    return positions


def parse_flex_position_values(xml_text: str) -> List[ParsedPositionValue]:
    root = ET.fromstring(xml_text)
    values: List[ParsedPositionValue] = []

    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in {"OpenPosition", "Position"}:
            continue
        instrument = _instrument_from_node(node)
        if instrument is None:
            continue
        quantity = _float(_attr(node, "position", "quantity", "qty"))
        value_native = _float(_attr(node, "positionValue"))
        value_base = _float(_attr(node, "positionValueInBase"))
        if quantity is None or value_native is None or value_base is None:
            continue
        account_id, account_label = _account(node)
        values.append(
            ParsedPositionValue(
                account_external_id=account_id,
                account_label=account_label,
                asset_class=instrument.asset_class,
                symbol=instrument.symbol,
                name=instrument.name,
                currency=instrument.currency,
                value_native=value_native,
                value_base=value_base,
                fx_rate_to_base=_float(_attr(node, "fxRateToBase")),
                quantity=quantity,
                conid=instrument.conid,
                mark_price=_float(_attr(node, "markPrice")),
                cost_basis_price=_float(_attr(node, "costBasisPrice")),
                fifo_pnl_unrealized=_float(_attr(node, "fifoPnlUnrealized")),
                unrealized_capital_gains_pnl=_float(_attr(node, "unrealizedCapitalGainsPnl")),
                unrealized_fx_pnl=_float(_attr(node, "unrealizedlFxPnl", "unrealizedFxPnl")),
            )
        )

    return values


def parse_flex_cash_report(xml_text: str) -> List[ParsedCashReport]:
    root = ET.fromstring(xml_text)
    cash_reports: List[ParsedCashReport] = []
    for node in root.iter():
        if node.tag.split("}")[-1] != "CashReportCurrency":
            continue
        level = (_attr(node, "levelOfDetail") or "").upper()
        if level != "CURRENCY":
            continue
        currency = (_attr(node, "currency") or "").upper()
        ending_cash = _float(_attr(node, "endingCash", "total", "cash", "settledCash"))
        if not currency or ending_cash is None:
            continue
        account_id, account_label = _account(node)
        cash_reports.append(
            ParsedCashReport(
                account_external_id=account_id,
                account_label=account_label,
                currency=currency,
                ending_cash=ending_cash,
                deposits=_float(_attr(node, "deposits", "depositWithdrawals")) or 0.0,
                withdrawals=_float(_attr(node, "withdrawals")) or 0.0,
                dividends=_float(_attr(node, "dividends")) or 0.0,
                from_date=_date(_attr(node, "fromDate")) if _attr(node, "fromDate") else None,
                to_date=_date(_attr(node, "toDate")) if _attr(node, "toDate") else None,
            )
        )
    return cash_reports


def parse_flex_cash_reports(xml_text: str) -> List[ParsedCashReport]:
    return parse_flex_cash_report(xml_text)


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
        proceeds = _float(_attr(node, "netCash", "proceeds", "amount"))
        if proceeds is None:
            proceeds = abs_qty * price * (1 if txn_type == "SELL" else -1)
        trade_cost = _float(_attr(node, "cost"))
        if trade_cost is None and txn_type == "BUY":
            trade_cost = abs_qty * price
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
                trade_cost=abs(trade_cost) if trade_cost is not None else None,
                commission=_float(_attr(node, "ibCommission", "commission")),
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
    raw_type = raw_type.upper()
    if "DEPOSIT" in raw_type and "WITHDRAW" in raw_type:
        return "DEPOSIT" if amount >= 0 else "WITHDRAWAL"
    if "DIV" in raw_type:
        return "DIVIDEND"
    if "FEE" in raw_type or "WITHHOLD" in raw_type or "TAX" in raw_type or "INTEREST" in raw_type:
        return "FEE"
    if "WITHDRAW" in raw_type:
        return "WITHDRAWAL"
    if "DEPOSIT" in raw_type or "EFT" in raw_type or "TRANSFER" in raw_type:
        return "DEPOSIT" if amount >= 0 else "WITHDRAWAL"
    return "DEPOSIT" if amount >= 0 else "WITHDRAWAL"


def _open_url(url: str, timeout: int = 30) -> str:
    context = None
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()

    with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
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
        raise RuntimeError(
            "IBKR Flex SendRequest did not return a ReferenceCode. %s"
            % _response_diagnostic(root)
        )


def _response_diagnostic(root: ET.Element) -> str:
    fields = []
    for name in (
        "Status",
        "ErrorCode",
        "ErrorMessage",
        "Message",
        "code",
        "message",
    ):
        value = _first_text(root, name)
        if value:
            fields.append("%s=%s" % (name, value))
    if fields:
        return "Response details: %s" % "; ".join(fields)
    root_tag = root.tag.split("}")[-1]
    child_tags = sorted({child.tag.split("}")[-1] for child in list(root)})
    return "Response root=%s child_tags=%s. Check token, query ID, token expiry, IP restriction, and Flex Web Service enablement." % (
        root_tag,
        ",".join(child_tags) if child_tags else "(none)",
    )


def _first_text(root: ET.Element, tag_name: str) -> Optional[str]:
    for node in root.iter():
        if node.tag.split("}")[-1].lower() == tag_name.lower() and node.text:
            return node.text.strip()
    return None


def today_snapshot_date() -> str:
    return dt.date.today().isoformat()
