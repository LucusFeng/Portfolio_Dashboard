# Dev Note: Manual Flex Upload and Reset DB

## Context

During real IBKR Flex testing, one Flex login could fail because IBKR could not generate the statement, while previously ingested data remained in SQLite. To make debugging easier during development, two support workflows were added:

- manually upload a downloaded IBKR Flex XML file
- reset the local development database from the dashboard

These are Phase 1 development tools. They are intentionally local-only.

## Manual IBKR Flex XML Upload

The dashboard now includes an **Upload Flex XML** form.

Route:

```text
POST /upload/flex
```

Inputs:

- `file`: downloaded IBKR Flex XML file
- `source_label`: a short label such as `login1` or `login2`

The upload route reads the XML file and sends it through the same parsing and ingestion path used by the live IBKR Flex API refresh:

- parse trades into normalized transactions
- parse open positions for reconciliation only
- parse position valuation fields
- parse cash reports
- insert deduplicated transactions
- rebuild lots and derived positions
- store broker position values
- store cash balances
- record position and cash reconciliation checks

Uploaded files use a manual source key, for example:

```text
manual_login1
```

This makes manual test data distinguishable from live API refresh data while still exercising the same parser and valuation code.

## Why This Helps

Manual upload lets us test the application even when IBKR Flex Web Service is temporarily failing with errors such as:

```text
ErrorCode=1001
Statement could not be generated at this time.
```

It also helps isolate whether an issue is caused by:

- the Flex query configuration
- the IBKR Web Service request flow
- the XML parser
- the transaction/lots/valuation logic

If the manually downloaded XML works but the API refresh fails, the parser is probably fine and the issue is likely on the IBKR request/configuration side.

## Reset DB Mechanism

The dashboard now includes a **Reset DB** button.

Route:

```text
POST /dev/reset-db
```

The reset action calls `reset_db(conn)`, which drops and recreates the local application tables using the current schema.

It clears development data from tables such as:

- accounts
- instruments
- transactions
- lots
- positions
- position values
- cash balances
- reconciliation records
- prices and FX rates
- ingestion runs

After the reset, the app records a new `dev_reset` run so the dashboard can show that the local database was intentionally cleared.

## Important Behavior

The reset action does not delete the SQLite file itself. It resets the tables inside the database file.

This is useful while schemas and ingestion behavior are still changing, because retained test data can make dashboard results misleading.

Typical development flow:

1. Click **Reset DB**.
2. Upload a known-good Flex XML file for `login1`.
3. Upload a known-good Flex XML file for `login2`.
4. Confirm consolidated holdings, account drilldowns, cash, and reconciliation output.
5. Try live **Refresh transactions** after the manual path is verified.

## Files Touched

- `app/routes/dashboard.py`
- `app/repository/db.py`
- `app/db.py`
- `templates/dashboard.html`
- `tests/test_dashboard_route_helpers.py`

## Notes

These controls are intended for local development only. Before any hosted or multi-user version exists, the reset route should either be removed, protected, or moved behind an explicit development-mode flag.
