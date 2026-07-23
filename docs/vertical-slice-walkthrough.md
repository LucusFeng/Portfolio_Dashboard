# Transaction-First Vertical Slice Walkthrough

This guide explains the current Phase 1 slice after the refactor. The key change is that
**transactions are now the source of truth**. Lots, positions, PnL, cash, and contribution
history are derived from the transaction ledger.

## Mental Model

```text
Browser
  -> route handlers
  -> services/internal API
  -> repository/database layer
  -> SQLite

External data
  -> ingestion adapters
  -> normalized transactions/reference/price observations
  -> repository writes
```

The important separation is:

- `app/routes`: FastAPI handlers only.
- `app/services`: portfolio, batch PnL, growth, and instrument enrichment logic.
- `app/repository`: all SQL.
- `app/ingestion`: IBKR, CIBC, Gateway, FX, and reference-data adapters.
- `app/models.py`: shared normalized dataclasses.

## What Changed From The First Slice

The original version stored broker position snapshots directly and valued those. The new
version stores transactions first:

```text
transactions
  -> lots
  -> positions
  -> dashboard valuation
```

Broker-reported positions are still useful, but only as a reconciliation check against
the derived positions.

## Main Database Tables

- `accounts`: brokerage accounts.
- `instruments`: canonical securities plus enrichable reference fields.
- `instrument_aliases`: broker symbols mapped to canonical instruments.
- `transactions`: immutable BUY/SELL/DEPOSIT/WITHDRAWAL/DIVIDEND/FEE rows.
- `lots`: open lots derived from BUY/SELL rows.
- `positions`: daily derived position snapshots.
- `prices`: append-only price observations.
- `fx_rates`: append-only FX observations.
- `reconciliations`: broker quantity vs derived quantity checks.
- `cash_balances`: latest broker-reported cash balances by account/currency.
- `cash_reconciliations`: broker-vs-derived balance and contribution checks.
- `ingestion_runs`: status messages for dashboard refresh actions.

The schema is versioned in `app/repository/db.py`. Because this was a development schema
change, old app tables are reset when the schema version changes.

## Request Flow

### Loading The Dashboard

```text
GET /
  -> app/routes/dashboard.py
  -> app/services/valuation.py
  -> portfolio + batch_pnl + growth services
  -> repository read functions
  -> templates/dashboard.html
```

The dashboard now shows:

- account summaries
- dedicated cash balances
- consolidated holdings
- account drilldown
- reconciliation warnings
- cash reconciliation warnings
- batch/open-lot PnL
- cumulative contributions

### Refreshing IBKR Transactions

```text
POST /refresh/transactions
  -> FlexClient fetches IBKR XML
  -> parse_flex_transactions() normalizes trades/cash flows
  -> transactions are deduped by source/external_id
  -> lots and positions are rebuilt
  -> Flex open positions are parsed for reconciliation
  -> Flex CashReportCurrency rows are stored as cash balances
  -> CashReportCurrency values are reconciled against dated cash transactions
  -> ingestion run status is recorded
```

The Flex positions section is no longer the valuation source. It is a cross-check.

### Uploading CIBC CSV

```text
POST /upload/cibc
  -> parse_cibc_transactions()
  -> append canonical transactions
  -> rebuild lots and positions
  -> record ingestion status
```

The parser is intentionally flexible because the exact CIBC export shape may need
adjustment once real sample files are available.

### Refreshing Prices

```text
POST /refresh/prices
  -> IBKR Gateway auth/status check
  -> fetch latest EOD marks by conid
  -> append price observations
  -> append manual USDCAD if configured
```

Prices remain independent from transaction ingestion. If the gateway is down, positions
and lots still exist; they simply show missing/stale marks.

### Enriching Reference Data

```text
POST /refresh/reference
  -> find instruments missing reference fields
  -> YFinanceProvider fetches sector/industry/country/market cap
  -> update instruments reference columns + JSON attributes
```

The provider is behind a small interface so yfinance can later be swapped for FMP,
Finnhub, or another provider.

## `.env` Values

```bash
DATABASE_PATH=data/portfolio.sqlite3
IBKR_GATEWAY_BASE_URL=https://localhost:5000/v1/api

IBKR_FLEX_LOGIN1_TOKEN=your_token
IBKR_FLEX_LOGIN1_QUERY_ID=your_query_id
IBKR_FLEX_LOGIN2_TOKEN=your_token
IBKR_FLEX_LOGIN2_QUERY_ID=your_query_id

MANUAL_USDCAD_RATE=1.35
```

`.env` is local-only and gitignored. It keeps secrets and machine-specific paths out of
the codebase.

## How Cash Works Now

Cash is no longer stored as a synthetic `CASH:*` position.

For dashboard display, cash comes from IBKR `CashReportCurrency endingCash`, stored in
`cash_balances` by account and currency. This makes broker cash the displayed cash source,
including negative USD margin balances.

Dated `CashTransactions` still matter. They restore the contribution series:

- `Deposits/Withdrawals` rows are summed with their signed amounts.
- Positive and negative reversal pairs net to zero.
- The cumulative contribution line can step down when a reversal or withdrawal appears.

Cash reconciliation writes two checks per account/currency:

- `balance`: CashReport `endingCash` vs full-ledger transaction sum.
- `contributions`: CashReport deposits/withdrawals vs dated deposit/withdrawal sum.

Differences above `1.0` are warnings. A contribution mismatch usually means the Flex query
date range does not cover full account history.

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
SELECT * FROM cash_balances;
SELECT * FROM reconciliations;
SELECT * FROM cash_reconciliations;
SELECT * FROM ingestion_runs;
.quit
```

## Tests

Run:

```bash
pytest
```

The suite covers:

- IBKR Flex trade/cash parsing
- broker position parsing for reconciliation
- CIBC CSV parsing
- transaction deduplication
- lot and position derivation
- cash balance display from CashReportCurrency
- cash reconciliation tolerance
- signed contribution series from dated CashTransactions
- batch PnL
- reference-data schema readiness

## Next Learning Steps

1. Read `app/routes/dashboard.py` to see the web actions.
2. Follow `POST /refresh/transactions` into `app/ingestion/ibkr_flex.py`.
3. Read `app/repository/positions.py` to understand lot and position derivation.
4. Read `app/services/portfolio.py` and `app/services/batch_pnl.py` for valuation logic.
5. Run tests while changing fixture data to see how the ledger reacts.
