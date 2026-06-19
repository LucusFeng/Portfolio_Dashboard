import datetime as dt
import json
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PriceMark:
    price: float
    as_of: str
    currency: str


class GatewayAuthError(RuntimeError):
    pass


class GatewayClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._ssl_context = ssl._create_unverified_context()

    def _request(self, method: str, path: str, params: Optional[Dict[str, str]] = None) -> Any:
        url = "%s/%s" % (self.base_url, path.lstrip("/"))
        if params:
            url = "%s?%s" % (url, urllib.parse.urlencode(params))
        request = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(request, timeout=30, context=self._ssl_context) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body else {}

    def ensure_authenticated(self) -> None:
        status = self._request("GET", "/iserver/auth/status")
        authenticated = bool(status.get("authenticated") or status.get("connected"))
        if not authenticated:
            raise GatewayAuthError("IBKR Gateway is not authenticated. Open the gateway and log in.")
        self._request("POST", "/tickle")

    def fetch_eod_price(self, conid: str, currency: str) -> PriceMark:
        self.ensure_authenticated()
        payload = self._request(
            "GET",
            "/iserver/marketdata/history",
            {
                "conid": conid,
                "period": "1d",
                "bar": "1d",
                "outsideRth": "true",
            },
        )
        bars: List[Dict[str, Any]] = payload.get("data") or []
        if not bars:
            raise RuntimeError("No history data returned for conid %s." % conid)
        bar = bars[-1]
        close = bar.get("c") or bar.get("close")
        if close is None:
            raise RuntimeError("History data for conid %s did not include a close price." % conid)
        as_of = str(bar.get("t") or dt.datetime.utcnow().isoformat())
        return PriceMark(price=float(close), as_of=as_of, currency=currency)


def current_fx_mark(manual_usdcad_rate: Optional[float]) -> Optional[PriceMark]:
    if manual_usdcad_rate is None:
        return None
    return PriceMark(
        price=float(manual_usdcad_rate),
        as_of=dt.datetime.utcnow().isoformat(timespec="seconds"),
        currency="CAD",
    )
