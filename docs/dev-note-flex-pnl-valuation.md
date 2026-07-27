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

## Verification

```bash
python3 -m pytest -q
python3 -m compileall -q app tests
```

Latest result:

```text
22 passed
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
- PnL gains are green and PnL losses are red in Account Drilldown and Batch PnL.
- Gateway price refresh does not try to price IBKR holdings.
