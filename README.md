# Portfolio Consolidation Dashboard

A local-first FastAPI web app that consolidates positions across brokerage accounts,
stores append-only daily snapshots in SQLite, fetches price/FX marks separately, and
computes CAD-normalized market value and unrealized PnL on read.

V1 is intentionally narrow: **IBKR first, server-rendered dashboard, stocks/ETFs/cash**.
CIBC CSV import, exposure analytics, live streaming, cloud hosting, and multi-user support
are deferred until the core ledger works end-to-end.

## What V1 Includes

- IBKR Flex XML ingestion for configured logins.
- Append-only SQLite tables for accounts, instruments, aliases, positions, prices, FX, and
  ingestion runs.
- IBKR Client Portal Gateway price refresh by `conid`.
- Manual USDCAD support via local env var.
- FastAPI + Jinja dashboard with account summaries, consolidated holdings, drilldown rows,
  refresh actions, and stale/missing mark indicators.

## Project Structure

```text
app/
  main.py                 FastAPI routes and refresh actions
  config.py               Local env/.env settings
  db.py                   SQLite connection and schema
  services/
    flex.py               IBKR Flex fetcher/parser
    pricing.py            IBKR Gateway client and FX helper
    repository.py         Append/upsert persistence helpers
    valuation.py          Read-time portfolio calculations
templates/
  dashboard.html          Server-rendered dashboard
tests/
  fixtures/sample_flex.xml
  test_flex.py
  test_repository_and_valuation.py
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a local `.env` file:

```bash
DATABASE_PATH=data/portfolio.sqlite3
IBKR_GATEWAY_BASE_URL=https://localhost:5000/v1/api

IBKR_FLEX_LOGIN1_TOKEN=your_token
IBKR_FLEX_LOGIN1_QUERY_ID=your_query_id
IBKR_FLEX_LOGIN2_TOKEN=your_token
IBKR_FLEX_LOGIN2_QUERY_ID=your_query_id

# Optional until automated FX is added.
MANUAL_USDCAD_RATE=1.35
```

Secrets stay local and `.env` is gitignored.

## Run

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

Use **Refresh positions** after configuring IBKR Flex credentials. Use **Refresh prices**
after starting and authenticating the IBKR Client Portal Gateway.

## IBKR Gateway Notes

1. Start the IBKR Client Portal Gateway.
2. Open `https://localhost:5000`, accept the local certificate, and log in.
3. The app checks `/iserver/auth/status`, calls `/tickle`, and then fetches delayed EOD
   history marks by `conid`.

If the gateway is not authenticated, the dashboard records an `auth_required` price run
instead of silently returning stale prices.

## Test

```bash
pytest
```

The tests cover Flex parsing, unsupported asset filtering, cash handling, append-only
snapshots, latest-value selection, FX conversion, and unrealized PnL math.

## Learning Guide

For a detailed walkthrough of what this vertical slice does and how the pieces fit
together, see [docs/vertical-slice-walkthrough.md](docs/vertical-slice-walkthrough.md).

## Next Steps

1. Run against real IBKR Flex XML and adjust parser field names if your Flex query uses
   different attributes.
2. Add automatic USDCAD fetching instead of the manual env var.
3. Add CIBC CSV import once the IBKR path is validated.
4. Add exposure views and historical charts after the ledger has several snapshots.
