# Dev Notes 2026-08-08

## Summary

This round stabilized local development and parser validation, simplified cash handling, and
improved API diagnostics. The manual XML path is working cleanly, but the IBKR Flex Web Service
API issue remains unresolved.

## 1. Manual Flex XML Upload

Added a dashboard form for manually uploading IBKR Flex XML files.

Route:

```text
POST /upload/flex
```

Inputs:

- `file`: downloaded Flex XML file
- `source_label`: label such as `login1` or `login2`

The uploaded XML uses the same ingestion pipeline as the API refresh:

- parse trades and cash transactions
- parse open positions for reconciliation
- parse Flex position valuation fields
- parse CashReport balances
- insert deduplicated transactions
- rebuild lots and derived positions
- store broker position values and cash balances

Manual testing confirmed this path works and is useful for isolating parser/database issues from
IBKR Web Service issues.

## 2. Reset DB Feature

Added a development-only reset button on the dashboard.

Route:

```text
POST /dev/reset-db
```

The route drops and recreates local app tables using the current schema, then records a
`dev_reset` ingestion run. This helped identify that some earlier confusing dashboard results
were caused by residual data retained in SQLite rather than parser defects.

The reset does not delete the SQLite file itself; it resets the tables inside it.

## 3. Cash Logic Simplification

Implemented the Simplify Cash spec.

Key decisions:

- `CashReportCurrency endingCash` is now the source of truth for displayed cash.
- Cash reconciliation was removed entirely.
- The `cash_reconciliations` table and repository functions were removed.
- The Cash Reconciliation dashboard section was removed.
- The Cash section now shows:
  - Account
  - CAD Cash
  - USD Cash
  - Net Cash CAD
  - Status

Net cash calculation:

```text
Net Cash CAD = CAD cash + USD cash * fxRateToBase
```

The FX rate comes from the latest USD `position_values.fx_rate_to_base`, sourced from Flex
`OpenPosition` rows. Negative USD cash remains signed, so margin debit reduces total cash and
the headline total.

If USD cash exists but no USD Flex position value exists anywhere, the account is flagged as
`needs FX` and excluded from cash total until a rate is available.

## 4. IBKR Flex API Work

Attempted to resolve the IBKR API issue by improving the API refresh workflow and diagnostics.

Changes added:

- `Refresh all` button.
- `Refresh login1` button.
- `Refresh login2` button.
- Per-login route:

```text
POST /refresh/transactions/{login_name}
```

- Partial success behavior preserved: one failed login does not erase previously stored data for
  another login.
- Added delay between login refreshes.

Active config knob:

```env
IBKR_FLEX_INTER_LOGIN_DELAY_SECONDS=15
```

Current status:

- Manual XML generation and upload work.
- Parser and valuation path appear healthy.
- Per-login API refresh still fails in current testing with:

```text
ErrorCode=1001
Statement could not be generated at this time. Please try again shortly.
```

Latest single-login failure confirmed that the issue is not only caused by calling both logins
together. The API problem is still outstanding.

## Follow-Up: Targeted API Revert

After later testing, the retry and long-polling API changes appeared to make the Flex Web Service
behavior worse: neither login refreshed successfully. We therefore reverted the `FlexClient`
API-call behavior back to the prior simpler implementation:

- one `SendRequest`
- no retry wrapper around `SendRequest`
- poll `GetStatement` up to 10 times
- 3-second sleep between polls

Kept from the API debugging work:

- `Refresh all`
- `Refresh login1`
- `Refresh login2`
- per-login refresh route
- partial success behavior
- masked query ID diagnostics

Removed from active runtime config:

- `IBKR_FLEX_STATEMENT_POLL_ATTEMPTS`
- `IBKR_FLEX_STATEMENT_POLL_INTERVAL_SECONDS`

Single-login refresh calls are now back to the prior simple Flex API behavior.

## Open Issue

The next debugging focus should be IBKR Flex Web Service behavior, not parser logic.

Possible next steps:

- Create a very small temporary Flex query, such as last 7 days or last 30 days, and test API
  refresh against only that query.
- Compare whether manual XML and Web Service generation differ for the same query.
- Avoid repeated rapid retries, because IBKR may escalate repeated failures into too-many-attempts
  lockout behavior.
- Keep using single-login refresh while diagnosing the Flex Web Service issue.

## Verification

Latest local test run after these changes:

```text
34 passed
```

Compile check also passed:

```bash
.venv/bin/python -m compileall -q app tests
```

## Files Of Interest

- `app/ingestion/ibkr_flex.py`
- `app/routes/dashboard.py`
- `app/repository/db.py`
- `app/repository/cash.py`
- `app/services/cash.py`
- `templates/dashboard.html`
- `tests/test_flex.py`
- `tests/test_dashboard_route_helpers.py`
- `tests/test_repository_and_valuation.py`
