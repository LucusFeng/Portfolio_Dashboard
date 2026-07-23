# Personal Investment Ecosystem

A local-first suite for managing personal portfolios and analyzing markets. It is built as
**two clusters of apps** over a shared foundation: a *portfolio-coupled* cluster (which reads
your holdings) and a set of *standalone market-data apps* (which do not). **Phase 1** is the
portfolio dashboard — the infrastructure layer the coupled cluster is built on, and it must
be solid before anything else is built.

---

## Vision

This is not a single app — it's an ecosystem. The pieces split by **one question: does this
app consume my portfolio, or just the market?** That line, not a phase number, is the real
architectural seam.

### Portfolio-coupled cluster (tabs in one frontend, one shared backend)

These read your holdings/transactions and join on *your* instruments. They share a service
layer (the internal API) and live as tabs in a single frontend.

- **Portfolio dashboard (Phase 1).** Unified positions, market value, and PnL across all
  accounts, normalized to CAD; batch (lot-level) PnL; cash; contributions; a daily
  value-vs-contributions history. *(This document's primary scope — the infrastructure the
  rest of the coupled cluster stands on.)*
- **Position management & grouping.** Group holdings by characteristics; manage exposure.
- **Stock analysis of holdings.** Factors driving the performance of securities you hold.
- **Option hedging ideas.** Hedge suggestions against *your* held exposure. (Sits on the
  seam — see below.)

### Standalone market-data apps (separate web apps, separate deploys)

These are functions of *the market*, not *your book*. They score or price a security whether
or not you hold it, so they are separate apps with their own backends.

- **Market sentiment.** A market-data pipeline: ingest feeds, score tickers, serve by symbol.
- **Option calculator.** Near-pure computation: spot/strike/expiry/vol/rate → price + greeks.

### How the two clusters relate (the dependency rule)

The standalone apps stay **portfolio-agnostic at their core**. When you want "sentiment for
*my* holdings" or "a hedge against *my* AAPL," the **coupled cluster passes instruments into
the standalone app** — the standalone app never reaches into your holdings. Dependency arrow:
**coupled → standalone, never the reverse.** This keeps the calculator an importable library,
sentiment a market pipeline, and your holdings from leaking into apps that shouldn't own that
concern.

**The one thing shared across the whole ecosystem is instrument identity** — the canonical
"what security is this" vocabulary that both clusters join on. For now it lives inside the
portfolio backend (see *Open Questions*), but its schema is treated as ecosystem-facing:
stable IDs, clean symbol/conid resolution, no portfolio-specific assumptions on the
instrument row — so extraction stays cheap if a second app ever needs to mint or resolve
instruments independently.

**Scope discipline is the real risk, not architecture.** The failure mode for an ambitious
solo project is building horizontally across every app at 20% depth and finishing none. The
rule: get Phase 1 genuinely *done* — cash correct, reconciliation trustworthy, daily history
captured — before any coupled-cluster Phase 2+ code, and before the standalone apps are more
than sketches. A rock-solid Phase 1 is what makes the rest credible.

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

The **portfolio-coupled cluster** is three layers that don't know about each other;
dependencies point downward only. The Phase 1 dashboard is just the *first consumer* of the
canonical store; every other coupled app is another consumer reading through the same service
interface. The **standalone apps** sit beside this stack, not on top of it — they are called
*into* by the coupled cluster (passed instruments), and share only instrument identity.

```
                                          coupled cluster calls  ┌──────────────────────────┐
                                          INTO standalone apps → │ STANDALONE MARKET-DATA   │
                                          (passing instruments)  │ APPS (separate deploys)  │
                                                                 │  • sentiment pipeline    │
COUPLED CLUSTER (one frontend, tabs)                             │  • option calculator     │
CONSUMERS   dashboard │ position mgmt │ stock analysis │ hedging │ (portfolio-agnostic core) │
                      │               │                │        └───────────┬──────────────┘
            ──────────┴───────────────┴────────────────┴──────┐             │
SERVICE     get_consolidated_positions() / get_pnl() ...      │             │ both join on
            (internal API — no SQL above this line)            │             │ instrument
            ───────────────────────────────────────────────── │             │ identity
DATA STORE  transactions → lots → positions │ cash │           │   ┌─────────┴─────────┐
            observations (prices, fx, ...)  │ instruments  ◄───┼───┤ INSTRUMENT        │
            (canonical, append-only; transactions are truth)  │   │ IDENTITY          │
            ───────────────────────────────────────────────── │   │ (ecosystem-shared;│
INGESTION   IBKR Flex │ CIBC CSV │ gateway prices │ FX │ ref   │   │ lives here for now)│
            (the only code that writes raw external data)      │   └───────────────────┘
```

**The dependency rule, restated:** coupled → standalone, never the reverse. Sentiment and the
calculator never import the portfolio backend; the dashboard (or hedging tab) hands them a
list of instruments and consumes their per-instrument output. **Hedging is the one app on the
seam** — it needs the calculator's machinery *and* your exposure — so the calculator's core is
kept as an importable, portfolio-agnostic library the coupled cluster can call, not only a web
app.

### Four decisions that make Phase 1 scale (cheap now, expensive to retrofit)

1. **Instrument reference data is a first-class, enrichable entity** — separate from
   positions. Grouping, factor analysis, sentiment, and hedging are all attributes *of a
   security*, independent of holdings. Structured columns for fields queried often
   (sector, industry, market cap, currency, conid) plus a flexible JSON `attributes`
   column for everything discovered later — new analysis dimensions without migrations.

2. **Every external fact is a timestamped, append-only observation** (`instrument_id` +
   `as_of` + `source`). Prices and FX establish the pattern in Phase 1; factor exposures and
   (in the standalone app) sentiment scores reuse the identical shape — giving historical
   series and backtestability "for free." This pattern is the platform's crown jewel: keep
   the observation table genuinely generalized, not a prices table wearing a coat, or every
   later series pays a migration tax. **Note:** an *opinion* (a hedge recommendation, a
   buy/sell idea) is **not** an observation — it's a recomputable service output and must
   never be persisted as if it were a fact. Store raw scores; compute opinions on read.

3. **A service layer sits between data and consumers.** The **coupled-cluster** apps call
   service functions, never SQL — this is the internal API they share, and the boundary must
   be hard *within* that cluster. (The standalone apps have their own backends and don't
   pressure this boundary; Python calls between coupled apps are fine for a long while, no
   premature REST needed.) A future REST API just wraps the service layer.

4. **Instrument identity is bulletproof, with a self-reference for derivatives.** A stable
   internal ID plus an optional `underlying_instrument_id` means options can point at their
   underlying without restructuring the core entity. Because instrument identity is the one
   join key shared across the *entire* ecosystem (both clusters), it is worth over-investing
   in relative to what the dashboard alone would justify — a messy instrument table poisons
   every app that joins on it.

---

## Open Questions (deliberately unresolved)

- **Extract instrument identity into its own shared service?** Today only the portfolio
  backend mints instruments, so extraction now would be premature abstraction — and the
  coupled → standalone dependency rule *reduces* the pressure (the dashboard hands sentiment
  a list of instruments, so sentiment needs no independent access to the identity layer).
  **Decision criterion:** revisit only when a *second* instrument-minting consumer appears
  (e.g. the sentiment app wants to discover and persist tickers you don't hold). Until then:
  design for extraction, don't extract. Keep the instrument row portfolio-agnostic so the
  future move is cheap.
- **Who may mint instruments?** If a standalone app ever discovers securities outside your
  holdings, that's a write path into the shared identity vocabulary. For now, the portfolio
  backend is the sole minter; standalone apps only *receive* identity.
- **Daily value history requires a scheduler, and it only fills forward.** See *Data
  Sources → Daily history & backfill* below. This is the main open build item gating the
  value-vs-contributions chart.

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
  executions section. Stateless token + query ID per login (login1 for RRSP + Margin, login2
  for Corporate).
- **CIBC (TFSA) — manual CSV upload.** Transaction-history CSV export; a parser maps CIBC's
  columns into the canonical transaction model.

**Cash — current state.** The live Flex query returns a **CashReport** (per-currency summary
totals: `endingCash`, `deposits`, `withdrawals`, `dividends`, …) but **no dated
`CashTransactions` rows** — Cash Transactions is a *separate, selectable* Flex section that
is not yet enabled. So today: cash *balances* and a contributions *total* come from the
CashReport summary; there is no dated per-deposit series. Enabling the **Cash Transactions**
section (with a date range back to account inception) unlocks the dated contributions series
— and that backfill is reconcilable against the CashReport `deposits` total. Trades already
provide full historical buy/sell detail regardless.

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

### Daily history & backfill (the time-series foundation)
The Flex refresh is best understood as an **EOD snapshot**: one click captures the current
state. Two different time series come out of this, with different mechanics:

- **Contribution series → backfillable from history *now*.** Once Cash Transactions is
  enabled, the entire dated deposit/withdrawal history reconstructs from Flex in one fetch —
  no waiting. Trades are already fully historical.
- **Portfolio *value* series → only fills forward.** Daily value is
  `positions × prices × FX` per day, and the gateway history endpoint as used
  (`period=1d, bar=1d`) captures ~today's close. Past daily marks can't be reconstructed by a
  scheduler that starts now — it accumulates going forward. (A one-time wider fetch, e.g.
  `period=1m, bar=1d`, can *seed* some recent price history to avoid starting the value line
  from a single point — coverage varies by holding.)

**Implication:** a value time-series dashboard needs a **scheduler** (APScheduler or an async
loop) firing the refresh once per EOD, writing one snapshot/day. Two operational notes: (1)
positions are keyed by `snapshot_date`, so the table already supports this — but the daily
series only densifies on days the job runs; (2) the **price** refresh needs an authenticated
gateway session that day (stateless Flex token handles transactions/cash unattended; prices
do **not** — session times out ~6 min idle, resets daily, needs manual 2FA). So a fully
hands-off EOD job is straightforward for transactions/cash and constrained for prices.

> **Persistence model at a glance.** `transactions` = append-only, deduped (never lost).
> `lots` = fully deleted & rebuilt each refresh (pure derivation). `positions` = overwritten
> *within* a `snapshot_date`, preserved *across* dates. `prices`/`fx_rates` = append-only by
> `as_of`. `reconciliations` = append-only, unbounded. `instruments`/`accounts` = upserted in
> place. Separately, a **schema-version bump wipes all app tables** (including `transactions`)
> and rebuilds — fine while Flex covers the needed range, but it means the local SQLite file
> is not yet a durable archive of source-of-truth data.

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

### Beyond Phase 1 (designed-for, not built)

**Coupled cluster (tabs, shared backend — read holdings):**
- [ ] Group positions by characteristics (queries over instrument reference data).
- [ ] Stock factor analysis for held securities.
- [ ] Option hedge *ideas* against held exposure (calls the standalone calculator's library;
      uses `underlying_instrument_id`; net delta-adjusted exposure). Opinions computed on
      read, never persisted as facts.
- [ ] Daily value-vs-contributions history (needs the scheduler; see *Daily history*).

**Standalone apps (separate deploys — market-data, portfolio-agnostic core):**
- [ ] Market sentiment pipeline (new observation table, same shape as prices; served by
      symbol; the coupled cluster passes in instruments to get "sentiment for my holdings").
- [ ] Option calculator (spot/strike/expiry/vol/rate → price + greeks; importable library
      *and* web app, so hedging can call it).

**Cross-cutting refinements:**
- [ ] Tax-accurate realized PnL (CRA adjusted cost base / ACB — average-cost, not FIFO);
      Phase 1's batch view is *unrealized* per-open-lot only.
- [ ] Money-weighted (IRR) / time-weighted returns, immune to deposit timing.
- [ ] Exposure/overlap analysis, tax-aware insights, scheduled EOD refresh.
- [ ] Durable archive of source-of-truth data that survives schema-version bumps.

---

## Next Steps
1. Scaffold the FastAPI project structure with the layer stubs.
2. Build the config table for the two Flex tokens + query IDs.
3. Build the Flex fetcher (trades + cash flows) + CIBC transaction parser into the normalizer.
4. Build lot derivation from the transaction log, and the derived position snapshot + Flex reconciliation.
5. Build the conid-based price fetcher (history endpoint first).
6. Build the reference-data enrichment behind the pluggable interface.
7. Wire up read-time computation, batch PnL, the value-vs-contributions series, and the dashboard.
