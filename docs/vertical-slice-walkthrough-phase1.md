# Transaction-First Vertical Slice Walkthrough - Phase 1

Official Phase 1 wrap-up date: **2026-08-21**.

This guide captures the completed Phase 1 vertical slice. The application is now a local,
transaction-first portfolio dashboard with IBKR Flex ingestion, manual XML fallback, CIBC CSV
upload, SQLite persistence, server-rendered dashboard views, dedicated cash handling, and
operational guardrails around the IBKR Flex API.

## Mental Model

```text
Browser
  -> FastAPI route handlers
  -> services/internal API
  -> repository/database layer
  -> SQLite

External data
  -> ingestion adapters
  -> normalized transactions/reference/price/cash observations
  -> repository writes
```

The important separation is:

- `app/routes`: thin FastAPI handlers and form actions.
- `app/services`: portfolio, cash, batch PnL, growth, valuation, and enrichment logic.
- `app/repository`: all SQL reads/writes.
- `app/ingestion`: IBKR Flex, CIBC CSV, Gateway, FX, and reference-data adapters.
- `app/models.py`: shared normalized dataclasses.
- `templates`: server-rendered Jinja dashboard.

## Phase 1 Scope

Phase 1 proves one end-to-end local workflow:

```text
IBKR/CIBC data
  -> normalized transactions and broker observations
  -> SQLite ledger
  -> derived lots/positions/cash
  -> CAD-normalized dashboard
```

The app is intentionally local-only. It is not a hosted, multi-user, real-time trading system.

## What Changed From The First Slice

The original version valued broker position snapshots directly. Phase 1 moved the source of
truth to the transaction ledger:

```text
transactions
  -> lots
  -> positions
  -> dashboard valuation
```

Broker-reported positions and broker-reported values are still stored, but they serve specific
roles:

- broker positions support reconciliation against derived positions
- IBKR Flex position values provide authoritative statement valuation fields
- Flex `fxRateToBase` supports CAD normalization for cash

## Main Database Tables

- `accounts`: brokerage accounts.
- `instruments`: canonical securities plus enrichable reference fields.
- `instrument_aliases`: broker symbols mapped to canonical instruments.
- `transactions`: append-only BUY/SELL/DEPOSIT/WITHDRAWAL/DIVIDEND/FEE rows.
- `lots`: open lots derived from BUY/SELL rows.
- `positions`: daily derived position snapshots.
- `position_values`: broker-reported Flex values, marks, FX, and PnL fields.
- `prices`: append-only price observations for non-Flex valuation paths.
- `fx_rates`: append-only FX observations where needed outside Flex statement FX.
- `reconciliations`: broker quantity vs derived quantity checks.
- `cash_balances`: broker-reported cash balances by account/currency.
- `ingestion_runs`: status messages for dashboard refresh/upload actions.

`cash_reconciliations` was removed during Phase 1. Cash is now broker-balance-first and does
not attempt to reconstruct ending cash from partial cash transaction history.

The schema is versioned in `app/repository/db.py`. Development DB resets are expected while
the schema evolves.

## Request Flow

### Loading The Dashboard

```text
GET /
  -> app/routes/dashboard.py
  -> app/services/valuation.py
  -> portfolio + cash + batch_pnl + growth services
  -> repository read functions
  -> templates/dashboard.html
```

The dashboard now shows:

- account summaries
- dedicated cash section
- consolidated holdings
- account drilldown
- position reconciliation warnings
- batch/open-lot PnL
- cumulative contributions
- manual refresh/upload/reset controls

### Refreshing IBKR Transactions

```text
POST /refresh/transactions
POST /refresh/transactions/login1
POST /refresh/transactions/login2
```

Flow:

```text
FlexClient fetches IBKR XML
  -> parse_flex_transactions() normalizes trades/cash flows
  -> transactions are deduped by source/external_id
  -> lots and positions are rebuilt
  -> Flex open positions are parsed for reconciliation
  -> Flex position values are stored for valuation/PnL
  -> CashReportCurrency rows are stored as cash balances
  -> ingestion run status is recorded
```

Per-login refresh was added because IBKR Flex Web Service can be timing-sensitive. The
separate buttons let login1 and login2 be tested independently.

### Manual IBKR Flex XML Upload

```text
POST /upload/flex
```

Manual upload uses the same parsing and ingestion pipeline as API refresh. This became an
important Phase 1 support workflow because manually downloaded Flex XML is reliable even when
IBKR's Web Service returns temporary statement-generation errors.

Typical dev flow:

1. Download Flex XML from IBKR.
2. Use **Upload Flex XML**.
3. Set the source label to `login1` or `login2`.
4. Confirm holdings, cash, valuation, and reconciliation output.

### Uploading CIBC CSV

```text
POST /upload/cibc
```

Flow:

```text
parse_cibc_transactions()
  -> append canonical transactions
  -> rebuild lots and positions
  -> record ingestion status
```

The CIBC parser remains intentionally flexible until more real exports are available.

### Refreshing Prices

```text
POST /refresh/prices
```

Flow:

```text
IBKR Gateway auth/status check
  -> fetch latest EOD marks by conid
  -> append price observations
  -> append manual USDCAD if configured
```

Price refresh remains independent from transaction ingestion. If Gateway auth is unavailable,
the transaction ledger still exists; affected rows simply show missing/stale marks.

### Enriching Reference Data

```text
POST /refresh/reference
```

Flow:

```text
find instruments missing reference fields
  -> YFinanceProvider fetches sector/industry/country/market cap
  -> update instruments reference columns + JSON attributes
```

The provider is behind a small interface so yfinance can later be swapped for another provider.

### Resetting The Development Database

```text
POST /dev/reset-db
```

The dashboard **Reset DB** button resets local SQLite app tables and records a `dev_reset` run.
It does not reset IBKR server-side statement-generation timing.

## `.env` Values

`.env` is local-only and gitignored. It keeps secrets and machine-specific paths out of the
codebase.

Current useful keys:

```bash
DATABASE_PATH=data/portfolio.sqlite3
IBKR_GATEWAY_BASE_URL=https://localhost:5000/v1/api

IBKR_FLEX_LOGIN1_TOKEN=your_token
IBKR_FLEX_LOGIN1_QUERY_ID=your_query_id
IBKR_FLEX_LOGIN2_TOKEN=your_token
IBKR_FLEX_LOGIN2_QUERY_ID=your_query_id

MANUAL_USDCAD_RATE=1.35
IBKR_FLEX_INTER_LOGIN_DELAY_SECONDS=15
IBKR_FLEX_REFRESH_COOLDOWN_SECONDS=60
```

Short aliases also work:

```bash
IBKR_FLEX_INTER_LOGIN_DELAY_SEC=15
IBKR_FLEX_REFRESH_COOLDOWN_SEC=60
```

## Local Folder

The active local repo is now:

```text
/Users/lukefeng/Code/Portfolio_Dashboard
```

The old Desktop/iCloud-backed copy should not be used for Git work. It showed File Provider
timeouts in `.git/objects`, which caused Git indexing and merge failures.

## How Cash Works Now

Cash is no longer stored as a synthetic `CASH:*` position.

For dashboard display, cash comes from IBKR `CashReportCurrency endingCash`, stored in
`cash_balances` by account and currency. This makes broker cash the displayed cash source,
including negative USD margin balances.

Net cash calculation:

```text
Net Cash CAD = CAD cash + USD cash * latest Flex USD fxRateToBase
```

Important details:

- USD cash remains signed.
- Negative USD margin debit reduces total cash and headline total.
- The FX rate comes from the latest USD `position_values.fx_rate_to_base`.
- If USD cash exists but no USD Flex FX rate exists, the row is flagged `needs FX`.
- Cash reconciliation warnings were removed because partial query ranges created false alarms.

Dated `CashTransactions` still matter for contribution history:

- `Deposits/Withdrawals` rows are summed with their signed amounts.
- Positive and negative reversal pairs net to zero.
- The cumulative contribution line can step down when a reversal or withdrawal appears.

## IBKR API Reality

The IBKR Flex Web Service should be treated as an **end-of-day batch source**, not a real-time
intraday data API.

Observed behavior:

- Manual XML upload is reliable.
- API refresh can work when triggered at the right time.
- Repeated refreshes can return:

```text
ErrorCode=1001
Statement could not be generated at this time. Please try again shortly.
```

The app added guardrails:

- per-login refresh buttons
- inter-login delay for **Refresh all**
- refresh cooldown guard
- throttle-aware messages for `1025` and `10010`

Even with these guardrails, DB reset does not reset IBKR's server-side generation state.

## How To Inspect The Database

```bash
sqlite3 data/portfolio.sqlite3
```

Useful queries:

```sql
.tables
SELECT * FROM transactions;
SELECT * FROM lots;
SELECT * FROM positions;
SELECT * FROM position_values;
SELECT * FROM cash_balances;
SELECT * FROM reconciliations;
SELECT * FROM ingestion_runs;
.quit
```

## Tests

Run:

```bash
python -m pytest -q
```

The suite covers:

- IBKR Flex trade/cash parsing
- Flex throttle-code diagnostics
- broker position parsing for reconciliation
- CIBC CSV parsing
- transaction deduplication
- lot and position derivation
- cash balance display from CashReportCurrency
- Net Cash CAD using Flex statement FX
- signed contribution series from dated CashTransactions
- batch PnL
- manual Flex XML upload path
- reset DB route
- per-login refresh routing
- cooldown/inter-login refresh behavior
- reference-data schema readiness

## Phase 1 Completion Notes

Phase 1 is considered wrapped as of **2026-08-21**.

Completed capabilities:

- transaction-first local portfolio ledger
- IBKR Flex API refresh
- per-login IBKR refresh
- manual IBKR Flex XML upload fallback
- CIBC CSV transaction upload
- SQLite persistence
- lots, positions, holdings, and batch PnL
- broker value/PnL display from Flex valuation fields
- dedicated broker-sourced cash handling
- Net Cash CAD
- contribution history
- reset DB development workflow
- server-rendered dashboard
- local documentation/dev notes

Recommended Phase 2 candidates:

- scheduled EOD refresh instead of manual API clicks
- richer CIBC parser hardening with more real exports
- better reference-data provider strategy
- UI polish and filtering
- export/reporting workflow
- more robust operational logging around IBKR timing failures
