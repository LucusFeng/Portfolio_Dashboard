# Dev Note - Flex PnL Valuation

## Summary

The dashboard now values IBKR holdings from IBKR Flex `OpenPosition` value fields instead of
reconstructing value from derived quantity, gateway price, and local FX.

This follows the locked decision in `flex-pnl-valuation-spec.md`: Flex is authoritative for
IBKR valuation, while the gateway price path remains only for non-Flex accounts such as CIBC.

## Key Decisions

- IBKR display rows are sourced from latest Flex `position_values`.
- Derived transaction positions remain useful for cost basis and reconciliation, but they do
  not decide whether an IBKR holding appears on the dashboard.
- IBKR CAD value uses `positionValueInBase` directly. The app does not apply USDCAD again.
- IBKR native value uses `positionValue` directly.
- CIBC/non-Flex holdings still use derived quantity multiplied by latest price and FX.
- Dashboard unrealized PnL remains hybrid for IBKR: Flex market value minus derived lot cost
  basis. If no derived cost basis exists, the holding still displays but PnL is blank.

## Data Model

Added `position_values`:

- `snapshot_date`
- `account_id`
- `instrument_id`
- `value_native`
- `value_base`
- `native_currency`
- `fx_rate_to_base`
- `quantity`
- `source`

SQLite schema version was bumped from `4` to `5`, so the disposable local dev DB will reset
on next app start.

## Code Changes

- Added `ParsedPositionValue` in `app/models.py`.
- Added `parse_flex_position_values` in `app/ingestion/ibkr_flex.py`.
- Added `app/repository/position_values.py` for storing and reading Flex value observations.
- Updated `/refresh/transactions` to store Flex position values with each Flex pull.
- Updated portfolio valuation so:
  - IBKR rows use Flex `positionValue` / `positionValueInBase`.
  - CIBC rows keep the existing price/FX valuation path.
  - IBKR rows can display even when derived transaction quantity is missing.
- Scoped `/refresh/prices` to non-IBKR derived positions.
- Added `Value source` columns to consolidated holdings and account drilldown.
- Updated batch PnL marks to use an implied unit mark from Flex value when available.

## Follow-up Refinements

- Consolidated holdings and account drilldown now show an implied IBKR Flex unit price when
  Flex value is available. The displayed price is `positionValue / quantity`, matching the
  same unit mark already used by Batch PnL.
- The implied price is display-only. IBKR CAD valuation still uses `positionValueInBase`
  directly and does not re-apply local USDCAD.
- PnL fields now use color to improve scanability:
  - positive PnL -> green
  - negative PnL -> red
  - zero or missing PnL -> default table text
- The color styling is scoped to PnL cells only, so negative cash balances or other signed
  values do not automatically inherit profit/loss styling.

## V3 Cost And PnL Refinement

Valuation V3 corrected the cost-basis source. USD cost no longer comes from raw trade price
or `costBasisPrice / fxRateToBase`. It now comes from Flex trade-level `cost`, which already
includes commission and matches IBKR's lot cost basis.

Key changes:

- Added `trade_cost` and `commission` to stored transactions.
- Added `cost_basis` and `remaining_cost_basis` to lots.
- BUY lots use Flex `Trade.cost`; if unavailable, the app falls back to `quantity * price`.
- Same-day buys still merge into one weighted-average batch.
- Partial sells reduce remaining lot cost basis proportionally.
- Added Flex OpenPosition fields to `position_values`: `mark_price`, `cost_basis_price`,
  `fifo_pnl_unrealized`, and optional capital-gains/FX PnL fields.
- Position market price prefers Flex `markPrice`, then falls back to `positionValue / quantity`.
- Position USD PnL is now `positionValue - sum(remaining lot cost)`.
- Position CAD PnL is now Flex `fifoPnlUnrealized`, matching the IBKR UI.
- Batch PnL now shows native/USD batch value and native/USD PnL. Per-lot CAD PnL remains
  deferred because Flex provides CAD PnL at the position level, not per lot.
- SQLite schema version was bumped from `5` to `6`.

## FX/Cash Instrument Exclusion

Flex can emit currency exchange activity as `<Trade assetCategory="CASH">` and sometimes as
cash-like open positions such as `USD.CAD`. These rows are not investable stock/ETF holdings
and should not appear in Batch PnL, Consolidated Holdings, or Account Drilldown.

The Flex instrument parser now treats only `EQUITY` and `ETF` as supported investable assets.
This excludes CASH/FX rows from:

- parsed broker positions used for reconciliation,
- parsed position values used for holdings valuation,
- parsed trade transactions used to rebuild lots and Batch PnL.

Cash balances and cash activity still use the dedicated cash paths:

- `CashReportCurrency` for displayed cash balances,
- `CashTransaction` for contributions/dividends/fees.

## Verification

```bash
python3 -m pytest -q
python3 -m compileall -q app tests
```

Latest result:

```text
23 passed
compileall passed
```

## Manual Retest

Reset the dev DB because schema version changed:

```bash
rm -f data/portfolio.sqlite3
python -m uvicorn app.main:app --reload
```

Then refresh transactions using the fixed IBKR Flex query.

Confirm:

- IBKR holdings show values before running gateway price refresh.
- Positions CAD matches the sum of IBKR `positionValueInBase` rows, plus any CIBC price/FX
  holdings.
- Total CAD equals positions CAD plus signed cash CAD.
- IBKR rows show `IBKR Flex` as value source.
- IBKR rows show a price derived from Flex `positionValue / quantity`.
- CIBC rows show `Price` as value source.
- Account Drilldown shows USD PnL and CAD PnL separately.
- Batch PnL shows batch cost, batch value, and USD/native PnL.
- PnL gains are green and PnL losses are red in Account Drilldown and Batch PnL.
- FX/CASH trades such as `USD.CAD` do not appear in Batch PnL or holdings tables.
- Gateway price refresh does not try to price IBKR holdings.
