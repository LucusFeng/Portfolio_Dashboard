from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class FlexStatementMetadata:
    to_date: Optional[str]
    when_generated: Optional[str]
    statement_count: int = 0
    pnl_ready: bool = True
    pnl_message: Optional[str] = None
    pnl_warning: Optional[str] = None


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
class ParsedChangeInNav:
    account_external_id: str
    account_label: str
    currency: str
    from_date: str
    to_date: str
    twr: Optional[float]
    starting_value: Optional[float] = None
    ending_value: Optional[float] = None
    deposits_withdrawals: Optional[float] = None
    dividends: Optional[float] = None
    mtm: Optional[float] = None
    interest: Optional[float] = None
    realized: Optional[float] = None
    change_in_unrealized: Optional[float] = None
    change_in_dividend_accruals: Optional[float] = None
    broker_fees: Optional[float] = None
    forex_commissions: Optional[float] = None
    fx_translation: Optional[float] = None
    cost_adjustments: Optional[float] = None


@dataclass(frozen=True)
class ParsedDailyNav:
    account_external_id: str
    account_label: str
    report_date: str
    currency: str
    nav: float
    cash: Optional[float] = None
    stock: Optional[float] = None
    dividend_accruals: Optional[float] = None
    interest_accruals: Optional[float] = None


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
    report_date: Optional[str] = None
    available_date: Optional[str] = None


@dataclass(frozen=True)
class InstrumentRef:
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    market_cap: Optional[float] = None
    name: Optional[str] = None
    extra: Dict[str, object] = field(default_factory=dict)
    source: str = ""
