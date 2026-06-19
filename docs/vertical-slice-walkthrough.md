# Vertical Slice Walkthrough

This document explains what has been built so far and how the pieces connect. It is
written as a learning guide, so it focuses on concepts and flow rather than only listing
files.

## What A Vertical Slice Means

A vertical slice is a small version of the real product that touches every important layer:

- A browser page you can open.
- A backend route that receives requests.
- Configuration loaded from your local machine.
- External API clients for broker/price data.
- A database that stores normalized records.
- Business logic that computes portfolio values.
- Tests that prove the core behavior works.

For this app, the slice is:

```text
Browser
  -> FastAPI route
  -> service layer
  -> SQLite database
  -> valuation logic
  -> Jinja HTML dashboard
```

The goal is not to finish every feature. The goal is to prove the main shape of the app
with one complete, working path.

## The Main Pieces

### `app/main.py`

This is the web app entrypoint. When you run:

```bash
uvicorn app.main:app --reload
```

Uvicorn imports `app/main.py`, finds the FastAPI object named `app`, and starts serving
HTTP requests.

Important routes:

- `GET /` renders the dashboard.
- `GET /health` returns a simple JSON health check.
- `POST /refresh/positions` fetches and stores IBKR Flex positions.
- `POST /refresh/prices` fetches and stores price/FX marks.

The browser mostly talks to these routes. The route functions then call lower-level
services to do the actual work.

### `app/config.py`

This file loads local settings. Settings are values that change between machines or should
not be committed to Git, such as tokens, database paths, and local gateway URLs.

The app looks for a `.env` file in the project root. Example:

```bash
DATABASE_PATH=data/portfolio.sqlite3
IBKR_GATEWAY_BASE_URL=https://localhost:5000/v1/api

IBKR_FLEX_LOGIN1_TOKEN=your_token
IBKR_FLEX_LOGIN1_QUERY_ID=your_query_id
IBKR_FLEX_LOGIN2_TOKEN=your_token
IBKR_FLEX_LOGIN2_QUERY_ID=your_query_id

MANUAL_USDCAD_RATE=1.35
```

Why `.env` matters:

- It keeps secrets out of source code.
- It lets you change local settings without editing Python files.
- It makes the same code runnable on another machine with different credentials.

The `.env` file is ignored by Git through `.gitignore`.

### `app/db.py`

This file owns the SQLite connection and schema.

SQLite is a single-file database. For this project, that means your portfolio data can
live in:

```text
data/portfolio.sqlite3
```

No database server is required.

The schema includes:

- `accounts`: real brokerage accounts.
- `instruments`: canonical securities, such as `AAPL` or `XIC`.
- `instrument_aliases`: broker-specific symbols mapped to canonical instruments.
- `position_snapshots`: append-only daily position records.
- `prices`: append-only price marks.
- `fx_rates`: append-only FX marks.
- `ingestion_runs`: status history for refresh actions.

Append-only means new rows are inserted instead of overwriting old rows. That makes it
possible to build historical charts later.

### `app/services/flex.py`

This file handles IBKR Flex.

It has two jobs:

1. Fetch Flex XML from IBKR using token/query ID.
2. Parse the XML into normalized Python objects.

The key object is `ParsedPosition`. It represents one normalized position before it is
written to the database.

Supported V1 asset types:

- Stocks
- ETFs
- Cash

Unsupported types, such as options, are ignored for now. That keeps the first version
small and easier to reason about.

### `app/services/repository.py`

This file writes normalized data into SQLite.

It contains functions like:

- `upsert_account`
- `upsert_instrument`
- `append_positions`
- `append_price`
- `append_fx_rate`

An "upsert" means:

```text
insert it if it does not exist;
otherwise update the existing row
```

That is useful for accounts and instruments, because you do not want duplicate account or
security rows every time you refresh.

Positions, prices, and FX rates are different. Those are appended each time because they
are time-based records.

### `app/services/pricing.py`

This file is the IBKR Client Portal Gateway client.

The gateway is a local service from IBKR. You log in through the browser, then this app
talks to the authenticated local gateway.

The app does not handle your IBKR password. It only calls local gateway endpoints after
you have logged in.

The flow is:

```text
Check /iserver/auth/status
  -> call /tickle to keep session alive
  -> fetch /iserver/marketdata/history by conid
```

If the gateway is not authenticated, the app records an `auth_required` run instead of
pretending prices refreshed successfully.

### `app/services/valuation.py`

This file computes the dashboard numbers.

It reads:

- the latest position snapshot for each account/instrument
- the latest price for each instrument
- the latest USDCAD FX rate

Then it computes:

- market value
- market value in CAD
- unrealized PnL
- account totals
- consolidated holdings
- missing price or missing FX warnings

Important design choice: market value and PnL are not stored in the database. They are
computed when the dashboard is loaded.

That keeps the database focused on facts:

- What did you hold?
- What was the price mark?
- What was the FX rate?

Derived values can always be recalculated later.

### `templates/dashboard.html`

This is the HTML page rendered by the backend.

The app uses Jinja templates, which means Python passes data into an HTML file and Jinja
fills in the dynamic parts.

Example idea:

```text
Python builds dashboard data
  -> passes it to dashboard.html
  -> Jinja loops over accounts and holdings
  -> browser receives normal HTML
```

This avoids needing React or frontend build tooling in V1.

## Request Flow

### Loading The Dashboard

When you open:

```text
http://127.0.0.1:8000
```

The flow is:

```text
Browser requests GET /
  -> FastAPI calls dashboard()
  -> app opens SQLite connection
  -> app initializes schema if needed
  -> valuation service builds dashboard data
  -> Jinja renders dashboard.html
  -> browser displays the page
```

### Refreshing Positions

When you click **Refresh positions**:

```text
Browser sends POST /refresh/positions
  -> app reads Flex credentials from .env
  -> FlexClient requests statement from IBKR
  -> parser converts XML into ParsedPosition objects
  -> repository upserts accounts/instruments
  -> repository appends position snapshots
  -> app records ingestion status
  -> browser redirects back to /
```

If there are no Flex credentials configured, the app records a skipped run and returns to
the dashboard.

### Refreshing Prices

When you click **Refresh prices**:

```text
Browser sends POST /refresh/prices
  -> app finds instruments with conid values
  -> GatewayClient checks IBKR gateway authentication
  -> app fetches EOD price marks by conid
  -> repository appends price rows
  -> app appends manual USDCAD if configured
  -> app records refresh status
  -> browser redirects back to /
```

If the gateway session has expired, the app records `auth_required`.

## Why Environment Variables Are Used

An environment variable is a named value available to a running process.

For example:

```bash
DATABASE_PATH=data/portfolio.sqlite3
```

The Python app can read that value using:

```python
os.getenv("DATABASE_PATH")
```

Environment variables are good for:

- secrets
- local paths
- API URLs
- feature flags
- values that differ between development and production

In this app, `.env` is a convenience file. Instead of manually exporting variables every
time, the app reads `.env` at startup.

## What An API Client Is

An API client is code that knows how to talk to another system.

This app has two API clients:

- `FlexClient` talks to IBKR Flex Web Service.
- `GatewayClient` talks to the local IBKR Client Portal Gateway.

The rest of the app should not need to know the exact URLs, query parameters, polling
rules, or JSON/XML shapes. That knowledge stays inside the client/service files.

This separation makes the app easier to test and change.

## How To Inspect The SQLite Database

After you run the app, the database is created at:

```text
data/portfolio.sqlite3
```

You can inspect it from Terminal:

```bash
sqlite3 data/portfolio.sqlite3
```

Useful commands inside SQLite:

```sql
.tables
SELECT * FROM accounts;
SELECT * FROM instruments;
SELECT * FROM position_snapshots;
SELECT * FROM prices;
SELECT * FROM fx_rates;
SELECT * FROM ingestion_runs;
.quit
```

This is one of the best ways to learn what the app actually wrote.

## Tests Included So Far

The tests are intentionally focused on the riskiest early logic:

- Flex XML parsing.
- Ignoring unsupported assets.
- Treating cash as price `1`.
- Appending snapshots instead of overwriting them.
- Choosing the latest snapshot/price.
- Converting USD values to CAD.
- Computing unrealized PnL.

Run them with:

```bash
pytest
```

Tests are important because financial dashboards can look correct while the math is wrong.
Small tests give you confidence before connecting real account data.

## Common Debugging Checklist

If the dashboard is blank:

- Confirm the server is running.
- Open `http://127.0.0.1:8000`.
- Check the dashboard message for the last ingestion run.
- Inspect `ingestion_runs` in SQLite.

If **Refresh positions** does nothing:

- Confirm `.env` exists in the project root.
- Confirm Flex token and query ID names match `app/config.py`.
- Check `ingestion_runs` for `skipped` or `failed`.

If **Refresh prices** says auth is required:

- Start IBKR Client Portal Gateway.
- Open `https://localhost:5000`.
- Accept the local certificate.
- Log in.
- Try **Refresh prices** again.

If USD holdings show missing FX:

- Add `MANUAL_USDCAD_RATE=1.35` to `.env`.
- Restart the FastAPI server.
- Refresh the dashboard.

## Suggested Learning Path

1. Read `app/main.py` and trace one route from top to bottom.
2. Read `app/config.py` and create your own `.env`.
3. Run the app and click each dashboard button.
4. Inspect `data/portfolio.sqlite3` with `sqlite3`.
5. Read `tests/test_repository_and_valuation.py` to understand the valuation math.
6. Modify `tests/fixtures/sample_flex.xml` and rerun tests to see how parsing behaves.
7. Only after that, connect real IBKR credentials.

## Mental Model

Think of the app as three layers:

```text
Web layer:
  FastAPI routes and Jinja templates

Data/source layer:
  IBKR Flex, IBKR Gateway, .env config

Core app layer:
  SQLite persistence and valuation logic
```

Keeping those layers separate is what makes the project easier to extend later. CIBC CSV,
automatic FX, charts, and exposure analysis can be added without rewriting the first
vertical slice.
