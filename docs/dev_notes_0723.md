# Dev Notes 2026-07-23

## Cash Refactor Summary

Cash handling was refactored so cash no longer appears as synthetic `CASH:*` instruments or
rows in `positions`. Holdings tables now contain only real instruments.

## Key Changes

- Added `cash_balances` as the authoritative display source for cash, populated from IBKR
  `CashReportCurrency endingCash`.
- Kept dated `CashTransactions` as the source for the contribution series.
- Added two cash reconciliation checks:
  - `balance`: broker `endingCash` vs full-ledger transaction sum.
  - `contributions`: broker deposits/withdrawals vs signed dated `DEPOSIT`/`WITHDRAWAL`
    transaction sum.
- Added `Cash` and `Cash Reconciliation` dashboard sections.
- Added `Contributions CAD` headline stat.
- Bumped SQLite schema to version `4`, which resets local dev app tables.

## Flex Parser Fixes

- Parses `CashReportCurrency` rows and skips base summary rows.
- Parses timestamped Flex dates like `20260611;202000` as `2026-06-11`.
- Maps `Broker Interest Paid` to `FEE` instead of `WITHDRAWAL`.
- Maps `Deposits/Withdrawals` by sign:
  - positive amount -> `DEPOSIT`
  - negative amount -> `WITHDRAWAL`
- Keeps signed contribution math, so offsetting reversal pairs net correctly.

## Files Of Interest

- `app/ingestion/ibkr_flex.py`
- `app/repository/cash.py`
- `app/services/cash.py`
- `app/repository/db.py`
- `app/routes/dashboard.py`
- `templates/dashboard.html`
- `tests/test_flex.py`
- `tests/test_repository_and_valuation.py`

## Verification

```bash
python3 -m pytest -q
python3 -m compileall -q app tests
```

Latest result:

```text
17 passed
```

## Manual Retest

Reset the dev database because schema version changed:

```bash
rm -f data/portfolio.sqlite3
python -m uvicorn app.main:app --reload
```

Then refresh transactions with the updated IBKR Flex query that includes:

- Trades
- Cash Transactions
- Open Positions
- Cash Report / `CashReportCurrency`

Confirm:

- Cash appears only in the Cash section.
- Holdings/drilldown have no `CASH:*` rows.
- Contributions use signed dated `CashTransactions`.
- Cash balances match IBKR `endingCash`.
- Cash reconciliation warnings appear only when differences exceed `1.0`.
