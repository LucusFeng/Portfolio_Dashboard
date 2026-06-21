from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class ParsedInstrument:
    asset_class: str
    symbol: str
    name: str
    currency: str
    conid: Optional[str] = None
    isin: Optional[str] = None


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


@dataclass(frozen=True)
class ParsedTransaction:
    txn_date: str
    broker: str
    account_external_id: str
    account_label: str
    tax_type: str
    txn_type: str
    amount: float
    currency: str
    source: str
    external_id: Optional[str] = None
    instrument: Optional[ParsedInstrument] = None
    quantity: Optional[float] = None
    price: Optional[float] = None


@dataclass(frozen=True)
class InstrumentRef:
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    market_cap: Optional[float] = None
    name: Optional[str] = None
    extra: Dict[str, object] = field(default_factory=dict)
    source: str = ""
