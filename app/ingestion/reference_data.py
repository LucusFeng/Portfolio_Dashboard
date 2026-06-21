from typing import Optional, Protocol

from app.models import InstrumentRef


class ReferenceProvider(Protocol):
    def fetch_reference(self, symbol: str) -> Optional[InstrumentRef]:
        ...


class YFinanceProvider:
    def fetch_reference(self, symbol: str) -> Optional[InstrumentRef]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("Install yfinance to use reference-data enrichment.") from exc

        info = yf.Ticker(symbol).info
        if not info:
            return None
        known = {"sector", "industry", "country", "marketCap", "longName"}
        return InstrumentRef(
            sector=info.get("sector"),
            industry=info.get("industry"),
            country=info.get("country"),
            market_cap=info.get("marketCap"),
            name=info.get("longName"),
            extra={key: value for key, value in info.items() if key not in known},
            source="yfinance",
        )
