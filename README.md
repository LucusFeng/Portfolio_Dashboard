# Personal Investment Ecosystem

A local-first platform that consolidates holdings across multiple brokerage accounts and
grows into a portfolio analysis system. **Phase 1** is a centralized portfolio dashboard —
the cornerstone that every later phase reads from.

---

## Vision

This is not a single app — it's the foundation of a personal investment ecosystem, built in
phases on one shared data store:

- **Phase 1 — Consolidated dashboard.** Unified positions, market value, and PnL across all
  accounts, normalized to CAD. Plus batch (lot-level) PnL tracking and a portfolio
  value-vs-contributions growth chart. *(This document's primary scope.)*
- **Phase 2 — Grouping.** Group positions into categories based on stock characteristics.
- **Phase 3 — Stock analysis.** Identify key factors that drive individual stock performance.
- **Phase 4 — Market sentiment.** Sentiment analysis layered onto holdings.
- **Phase 5 — Option hedging.** Hedge recommendations against held exposure.

Phases 2–5 contain real nuance and will be designed later. Phase 1's job is to build the
**platform** so those phases are *additive*, not rewrites.

---

## The Problem

Investments are spread across four siloed brokerage accounts:

| Account | Broker | Login | Tax type |
|---|---|---|---|
| TFSA | CIBC Investor's Edge | — (manual) | TFSA |
| RRSP | IBKR | login1 | Registered |
| Margin | IBKR | login1 | Taxable |
| Corporate Investment | IBKR | login2 | Corporate |

Each account lives behind its own login and interface, so there's no single view of
**overall exposure** — total positions, market value, PnL, currency mix, or overlap (e.g.
the same ETF held in two accounts). That makes portfolio management slower and decisions
less informed. This platform ingests all four, normalizes them into one canonical model,
and presents a unified dashboard — then becomes the base for deeper analysis.

This is also a deliberate learning project: hands-on work with real financial APIs, data
modeling, and a Python web backend.

---

## Architecture: Build a Platform, Not an App

Three layers that don't know about each other. Dependencies point downward only. The
Phase 1 dashboard is just the *first consumer* of the canonical store; every later phase is
another consumer reading through the same service interface.

```
CONSUMERS        Phase 1 dashboard │ P2 groups │ P3 factors │ P4 sentiment │ P5 hedging
                                   │           │            │              │
                 ──────────────────┴───────────┴────────────┴──────────────┴────────
SERVICE LAYER    get_consolidated_positions() / get_instrument() / get_pnl() ...
                 (the internal API — no SQL above this line)
                 ────────────────────────────────────────────────────────────────────
DATA STORE       transactions → lots → positions │ instruments │ observations (prices, fx, ...)
                 (canonical, append-only; transactions are the source of truth)
                 ────────────────────────────────────────────────────────────────────
INGESTION        IBKR Flex │ CIBC CSV │ IBKR gateway prices │ FX │ reference data
                 (the only code that writes raw external data)
```

### Four decisions that make Phase 1 scale (cheap now, expensive to retrofit)

1. **Instrument reference data is a first-class, enrichable entity** — separate from
   positions. Grouping, factor analysis, sentiment, and hedging are all attributes *of a
   security*, independent of holdings. Structured columns for fields queried often
   (sector, industry, market cap, currency, conid) plus a flexible JSON `attributes`
   column for everything discovered later — new analysis dimensions without migrations.

2. **Every external fact is a timestamped, append-only observation** (`instrument_id` +
   `as_of` + `source`). Prices and FX establish the pattern in Phase 1; sentiment and
   factor exposures reuse the identical shape later — giving historical series and
   backtestability "for free."

3. **A service layer sits between data and consumers.** The dashboard (and every later
   phase) calls service functions, never SQL. The service layer is the internal API; a
   future REST API just wraps it.

4. **Instrument identity is bulletproof, with a self-reference for derivatives.** A stable
   internal ID plus an optional `underlying_instrument_id` means Phase 5 options can point
   at their underlying without restructuring the core entity.

---

## Key Design Principles

- **Local-first.** Aggregates among the most sensitive data owned; runs entirely on
  `localhost`. No cloud server, no public attack surface, no tokens on a remote host.
- **Separate the slow layer from the fast layer.** *Positions* (what is held) change only
  on trades → captured as a **daily snapshot**. *Prices* (what it's worth) move
  continuously → fetched independently and more often. Different sources, refresh rates,
  and auth models; never conflated.
- **Append-only storage; compute on read.** Market value and PnL are never stored — they're
  derived live from latest snapshot × latest price × latest FX rate.
- **Normalize to a canonical instrument.** Per-broker ticker aliases and a cached IBKR
  `conid` make cross-account aggregation trustworthy.
- **Provenance everywhere.** A `source` column on all observation and reference data, so
  values are traceable and providers are swappable.

---

## Data Sources

### Transactions (the source of truth)
Batch PnL and the growth chart require the underlying **transaction history**, not just
aggregate positions — average cost throws away the per-batch detail. So transactions are
the foundation: positions are *derived* from them (sum the lots), not vice versa.

- **IBKR (RRSP, Margin, Corporate) — Flex Web Service.** The Flex query includes a trades /
  executions section plus a cash-transactions (deposits / withdrawals) section. Stateless
  token + query ID per login (login1 for RRSP + Margin, login2 for Corporate).
- **CIBC (TFSA) — manual CSV upload.** Transaction-history CSV export; a parser maps CIBC's
  columns into the canonical transaction model.

Transactions cover buys, sells, deposits, withdrawals, dividends, and fees. Deposits and
withdrawals do double duty — they're both the contributions series and part of the full
cash-flow picture.

### Positions (derived + reconciled)
- Positions are **derived** from the transaction log as a daily snapshot.
- The Flex *positions* section is still pulled directly as a **cross-check** — reconciling
  "sum of my lots" against "what IBKR says I hold" is a useful data-integrity test.
- Daily snapshot only — Flex is rate-limited, not built for frequent polling.

### Prices
- **IBKR Client Portal Gateway**, fetched **by conid** (so the same lookup marks both IBKR
  and CIBC holdings). No market-data subscription held → using **delayed end-of-day closes**
  via the history endpoint (`/iserver/marketdata/history`), which sidesteps the snapshot
  endpoint's prime-then-read flow and is sufficient for daily marks.
- **FX (USDCAD)** fetched alongside, stored append-only.

### Reference data (sector, industry, market cap, country)
IBKR is **not** used for this. IBKR returns contract-level basics (asset class, exchange)
but real fundamentals require paid research subscriptions and the stateful gateway — wrong
tool for the descriptive layer. Instead, a dedicated reference provider is used, behind a
pluggable interface:

| Provider | Notes |
|---|---|
| **yfinance** | Recommended for V1. No API key; covers sector, industry, market cap, country. Unofficial (scrapes Yahoo) — can break without notice, but fine for periodic local enrichment. Handles TSX via `.TO` suffix. |
| **FMP** | Durable upgrade. Fundamentals specialist, SEC-sourced (strong on US equities). Official key won't silently break. Free tier with rate limits. |
| **Finnhub** | Free tier ~60 calls/min, international coverage, company-profile endpoint. |
| **Alpha Vantage** | Beginner-friendly; free tier ~5 req/min, 500/day — fine for slow enrichment only. |

**Coverage caveat:** the CIBC TFSA holds TSX-listed securities. US-filing-based sources
(FMP) are weakest here — verify Canadian-listing coverage against actual holdings before
committing. yfinance/EODHD tend to handle TSX better.

Reference data changes slowly (a company's sector rarely moves), so it's fetched once per
instrument and refreshed rarely — tight free-tier rate limits are a non-issue.

---

## Data Model (SQLite)

```sql
CREATE TABLE accounts (
    id            INTEGER PRIMARY KEY,
    broker        TEXT NOT NULL,        -- 'IBKR' | 'CIBC'
    external_id   TEXT,                 -- IBKR accountId; null for CIBC
    label         TEXT NOT NULL,        -- 'TFSA', 'RRSP', 'Margin', 'Corp'
    tax_type      TEXT NOT NULL,        -- 'TFSA' | 'RRSP' | 'MARGIN' | 'CORP'
    base_currency TEXT NOT NULL
);

-- Canonical security identity + enrichable reference data.
CREATE TABLE instruments (
    id                     INTEGER PRIMARY KEY,
    symbol                 TEXT,         -- canonical symbol
    name                  TEXT,
    asset_class           TEXT,          -- 'EQUITY' | 'ETF' | 'OPTION' | 'CASH'
    currency              TEXT NOT NULL, -- trading currency
    conid                 INTEGER,       -- cached IBKR contract id (for pricing)
    isin                  TEXT,
    figi                  TEXT,
    -- reference attributes (queried often -> real columns)
    sector                TEXT,
    industry              TEXT,
    country               TEXT,
    market_cap            REAL,
    -- derivatives: self-reference to the underlying instrument
    underlying_instrument_id INTEGER REFERENCES instruments(id),
    -- everything else a provider returns, for later use, no migration needed
    attributes            TEXT,          -- JSON blob
    ref_source            TEXT,          -- provenance: 'yfinance' | 'fmp' | ...
    ref_as_of             TEXT           -- when reference data was last fetched
);

CREATE TABLE instrument_aliases (
    broker        TEXT NOT NULL,
    broker_symbol TEXT NOT NULL,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    PRIMARY KEY (broker, broker_symbol)
);

-- FOUNDATION: immutable transaction log. The source of truth.
CREATE TABLE transactions (
    id            INTEGER PRIMARY KEY,
    txn_date      TEXT NOT NULL,
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id INTEGER REFERENCES instruments(id),  -- null for pure cash flows
    txn_type      TEXT NOT NULL,   -- 'BUY'|'SELL'|'DEPOSIT'|'WITHDRAWAL'|'DIVIDEND'|'FEE'
    quantity      REAL,            -- null for cash flows
    price         REAL,            -- per-unit, null for cash flows
    amount        REAL NOT NULL,   -- signed cash impact, in currency
    currency      TEXT NOT NULL,
    source        TEXT,            -- 'ibkr_flex' | 'cibc_csv'
    external_id   TEXT             -- broker txn id; dedups overlapping re-imports
);

-- Open lots: derived from BUY/SELL transactions. Drives batch PnL.
-- Start as a computed VIEW rebuilt on ingest (never drifts); promote to a
-- materialized table only if performance ever demands it (it won't, at this scale).
CREATE TABLE lots (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id   INTEGER NOT NULL REFERENCES instruments(id),
    open_date       TEXT NOT NULL,
    open_quantity   REAL NOT NULL,   -- original batch size
    remaining_qty   REAL NOT NULL,   -- after partial sells
    cost_per_unit   REAL NOT NULL,
    cost_currency   TEXT NOT NULL,
    open_txn_id     INTEGER REFERENCES transactions(id)
);

-- DERIVED: daily position snapshots. Append-only.
-- Computed from transactions; also pulled directly from Flex for reconciliation.
CREATE TABLE positions (
    id            INTEGER PRIMARY KEY,
    snapshot_date TEXT NOT NULL,
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    quantity      REAL NOT NULL,
    avg_cost      REAL NOT NULL,
    cost_currency TEXT NOT NULL,
    UNIQUE (snapshot_date, account_id, instrument_id)
);

-- FAST layer: price marks. Append-only. The observation pattern.
CREATE TABLE prices (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    as_of         TEXT NOT NULL,
    price         REAL NOT NULL,
    currency      TEXT NOT NULL,
    source        TEXT,                  -- 'ibkr_history' | 'ibkr_snapshot' | ...
    PRIMARY KEY (instrument_id, as_of)
);

CREATE TABLE fx_rates (
    pair   TEXT NOT NULL,                -- 'USDCAD'
    as_of  TEXT NOT NULL,
    rate   REAL NOT NULL,
    source TEXT,
    PRIMARY KEY (pair, as_of)
);
```

Future observation tables (sentiment, factor exposures) reuse the `prices` shape exactly:
`instrument_id` + `as_of` + `source` + value columns.

**No separate contributions table.** The contributions series is a query — the cumulative
sum of `DEPOSIT`/`WITHDRAWAL` amounts from `transactions` over time. Contributions are
modeled as cash flows, not returns: a $5,000 deposit steps *both* the value line and the
contribution line up by $5,000 on that day, so the gap between them only widens from market
movement — that gap is the real investment gain. (A deposit-timing-immune *return* figure —
money-weighted IRR or time-weighted return — is a later refinement.)

---

## The Reference-Data Interface (pluggable provider)

Reference providers are the component most likely to change (free tiers shift, scrapers
break, coverage gaps appear), so they sit behind one small interface. Swapping yfinance for
FMP later touches exactly one file.

```python
# ingestion/reference_data.py
from dataclasses import dataclass, field
from typing import Protocol

@dataclass
class InstrumentRef:
    """Normalized reference data, provider-agnostic."""
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    market_cap: float | None = None
    name: str | None = None
    extra: dict = field(default_factory=dict)   # -> instruments.attributes (JSON)
    source: str = ""                             # -> instruments.ref_source

class ReferenceProvider(Protocol):
    def fetch_reference(self, symbol: str) -> InstrumentRef | None:
        ...

# --- one concrete provider; add others without touching callers ---
class YFinanceProvider:
    def fetch_reference(self, symbol: str) -> InstrumentRef | None:
        import yfinance as yf
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
            extra={k: v for k, v in info.items() if k not in known},
            source="yfinance",
        )
```

The enrichment step calls `provider.fetch_reference(symbol)` for any instrument missing
reference data, writes the structured fields to `instruments` columns and `extra` to the
JSON `attributes`, and stamps `ref_source` + `ref_as_of`. Run once per new instrument;
refresh rarely.

---

## Project Structure

```
app/
  repository/         # ALL SQL lives here
    db.py
    instruments.py
    transactions.py   # the transaction log + lot derivation
    positions.py      # derived snapshots + Flex reconciliation
    observations.py   # prices, fx — generalized, not price-specific
  ingestion/          # external world -> repository
    ibkr_flex.py      # trades, cash transactions, positions (cross-check)
    ibkr_gateway.py
    cibc_csv.py       # positions + transaction-history parsers
    fx.py
    reference_data.py # pluggable reference provider(s)
    normalizer.py     # broker data -> canonical instruments
  services/           # business logic, the internal API, NO SQL
    portfolio.py      # get_consolidated_positions, get_pnl
    batch_pnl.py      # get_batch_pnl (lot mark vs cost)
    growth.py         # get_value_vs_contributions (two time series)
    instruments.py    # get_instrument, enrich_instrument
  routes/             # thin FastAPI handlers, NO logic, NO SQL
    dashboard.py
  models/             # shared dataclasses / pydantic schemas
  config.py
  main.py
```

Build Phase 1's functionality simply — don't pre-build sentiment tables or a grouping
engine. Just keep the *seams* (observation pattern, JSON attributes, service interface,
pluggable providers) clean so later phases attach without restructuring.

---

## Setup

For step-by-step terminal commands, including virtual environment setup and Anaconda
troubleshooting, see [docs/run-from-terminal.md](docs/run-from-terminal.md).

### Stack
- **Backend:** Python + FastAPI
- **Storage:** SQLite (zero-config, single-user, local)
- **Frontend:** TBD (server-rendered templates for V1, or React)
- **Scheduler (later):** APScheduler or async loop for periodic price refresh

### Prerequisites
- Python environment
- Java runtime (for the IBKR Client Portal Gateway)
- Two IBKR Flex tokens + query IDs (one per login), from IBKR Account Settings
- CIBC holdings CSV export
- (Optional) a reference-data API key if upgrading past yfinance

### Secrets
Flex tokens, API keys, and config live in a **local-only** gitignored file or environment
variables — never committed. Flex tokens rotate (~annually), refreshed independently per
login.

### Running the price gateway
1. Download, unzip, start the IBKR Client Portal Gateway (`bin/run.sh root/conf.yaml`).
2. Open `https://localhost:5000`, accept the self-signed cert, log in with IBKR
   credentials (2FA required). The app never sees the password — it talks only to the
   authenticated local gateway.
3. The app checks `GET /iserver/auth/status` and calls `POST /tickle` (~every minute) to
   keep the session alive; sessions time out after ~6 minutes idle and reset daily. If the
   session has dropped, the app prompts for re-login rather than returning stale prices.

> **Two logins:** the gateway authenticates one login at a time. Prices are fetched by
> conid (account-agnostic), so only one login needs to be active to cover the union of held
> instruments. Account separation already happens in the Flex/positions layer.

---

## Requirements & Objectives

### Phase 1 — Functional
- [ ] Ingest IBKR transactions (trades + cash flows) from both logins via Flex.
- [ ] Ingest CIBC transactions + positions via CSV upload + parsers.
- [ ] Dedup transactions on re-import via `external_id`.
- [ ] Derive open lots and daily position snapshots from the transaction log.
- [ ] Reconcile derived positions against Flex position snapshots (data-integrity check).
- [ ] Normalize all holdings to canonical instruments (alias + conid resolution).
- [ ] Enrich instruments with reference data (sector, industry, market cap) via pluggable provider.
- [ ] Fetch delayed EOD prices by conid from the IBKR gateway.
- [ ] Fetch USDCAD FX rate.
- [ ] Consolidated dashboard: all positions, per-account and total market value,
      unrealized PnL, CAD-normalized grand total.
- [ ] **Batch PnL view:** each open lot marked to current price, gain vs cost to today.
- [ ] **Growth chart:** portfolio total value vs cumulative contributions over time.
- [ ] Manual "refresh" for transactions and prices.

### Phase 1 — Non-functional / platform
- [ ] Runs entirely locally; no data or secrets leave the machine.
- [ ] Append-only data; no destructive overwrites.
- [ ] Three-layer separation (routes / services / repository); no SQL above the service layer.
- [ ] Observation pattern + JSON attributes + pluggable providers in place as extension seams.
- [ ] Resilient to a dropped price session (clear re-auth prompt, no silent stale data).
- [ ] Position snapshot keeps working even if the price gateway is down.

### Later phases (designed-for, not built)
- [ ] **P2:** Group positions by characteristics (queries over instrument reference data).
- [ ] **P3:** Individual stock factor analysis.
- [ ] **P4:** Market sentiment (new observation table, same shape as prices).
- [ ] **P5:** Option hedge recommendations (uses `underlying_instrument_id`; net delta-adjusted exposure).
- [ ] Tax-accurate realized PnL (CRA adjusted cost base / ACB — average-cost, not FIFO);
      Phase 1's batch view is *unrealized* per-open-lot only.
- [ ] Money-weighted (IRR) / time-weighted returns, immune to deposit timing.
- [ ] Exposure/overlap analysis, tax-aware insights, scheduled price refresh.

---

## Next Steps
1. Scaffold the FastAPI project structure with the layer stubs.
2. Build the config table for the two Flex tokens + query IDs.
3. Build the Flex fetcher (trades + cash flows) + CIBC transaction parser into the normalizer.
4. Build lot derivation from the transaction log, and the derived position snapshot + Flex reconciliation.
5. Build the conid-based price fetcher (history endpoint first).
6. Build the reference-data enrichment behind the pluggable interface.
7. Wire up read-time computation, batch PnL, the value-vs-contributions series, and the dashboard.
