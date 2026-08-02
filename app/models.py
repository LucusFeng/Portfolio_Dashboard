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
class ParsedPositionValue:
    account_external_id: str
    account_label: str
    asset_class: str
    symbol: str
    name: str
    currency: str
    value_native: float
    value_base: float
    fx_rate_to_base: Optional[float]
    quantity: float
    conid: Optional[str]
    mark_price: Optional[float] = None
    cost_basis_price: Optional[float] = None
    fifo_pnl_unrealized: Optional[float] = None
    unrealized_capital_gains_pnl: Optional[float] = None
    unrealized_fx_pnl: Optional[float] = None


@dataclass(frozen=True)
class ParsedCashReport:
    account_external_id: str
    account_label: str
    currency: str
    ending_cash: float
    deposits: float = 0.0
    withdrawals: float = 0.0
    dividends: float = 0.0
    from_date: Optional[str] = None
    to_date: Optional[str] = None


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
    trade_cost: Optional[float] = None
    commission: Optional[float] = None


@dataclass(frozen=True)
class InstrumentRef:
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    market_cap: Optional[float] = None
    name: Optional[str] = None
    extra: Dict[str, object] = field(default_factory=dict)
    source: str = ""
