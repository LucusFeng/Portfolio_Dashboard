# Dev Spec — Portfolio Valuation from Flex `positionValue`

## Goal

Make the dashboard mirror IBKR. Today, IBKR position value is computed as
`derived quantity × gateway price × self-applied FX` — three sources that can disagree.
Switch IBKR valuation to IBKR's own reported figures from the Flex `OpenPosition` node
(`positionValue`, `positionValueInBase`), and scope the gateway price path to the only
account that has no Flex query (CIBC/TFSA).

**Principle:** the portfolio dashboard leverages Flex as much as possible and does **not**
use external/live market data. External market data belongs to the standalone tools
(sentiment, option calculator), never the portfolio view.

---

## What the Flex export provides (verified against `testing_v3_0723.xml`)

Each `OpenPosition` node carries value **and** cost **and** PnL, already currency-resolved:

| Attribute | Meaning | Sample (AMZN) |
|---|---|---|
| `positionValue` | market value, **native currency** | 1224.25 (USD) |
| `positionValueInBase` | market value, **base currency (CAD)** | 1724.60 |
| `fxRateToBase` | per-row FX native→base | 1.4087 |
| `currency` | native currency | USD |
| `position` | quantity | 5 |
| `costBasisPrice` | per-unit cost | 332.0165 |
| `fifoPnlUnrealized` | IBKR's unrealized PnL (native) | 64.52 |
| `markPrice` | **empty in this export** | (absent) |

**Critical:** `markPrice` is absent but `positionValue` is populated. The parser must read
`positionValue` / `positionValueInBase` **directly** and must NOT reconstruct value from
`price × quantity` (there is no price to use).

Account total for this file: positions **26,043.19 USD** (native) /
**≈36,684 CAD** (sum of `positionValueInBase`); cash **+7,122.97 CAD** and
**−9,657.98 USD** (margin debit).

---

## Decisions (locked)

1. **IBKR value = Flex, authoritative.** Native from `positionValue`, CAD from
   `positionValueInBase` (IBKR's own conversion — do **not** re-apply our USDCAD to IBKR
   positions; summing `positionValueInBase` keeps the CAD total matching IBKR exactly).
2. **Derived quantity is a reconciliation check only** for IBKR. When derived qty (from
   transactions) disagrees with Flex `position`, Flex wins for display; the difference
   surfaces as a reconciliation warning (existing mechanism).
3. **CIBC value = derived qty × gateway price × FX.** CIBC has no Flex query, so it still
   needs an externally-fetched price. This is the *only* account that uses the gateway.
   Low-volume, seeded once, rarely refreshed.
4. **Gateway scoped to non-Flex accounts only.** Not a general price path, not a generic
   "fallback." `pricing.py` / `ibkr_gateway.py` survive solely to value accounts that have no
   Flex-reported value (today: CIBC). IBKR valuation never calls the gateway → the IBKR EOD
   scheduler stays fully hands-off (no 2FA/session dependency).
5. **Total value = gross positions + cash, shown separately.** Do not net the margin debit
   into positions. Cash section already shows CAD/USD separately including negatives (from the
   cash refactor). Grand total = positions_total_cad + cash_total_cad (cash total is signed,
   so the negative USD cash reduces it).
6. **PnL stays derived from cost basis** (consistent across IBKR and CIBC). See the important
   note below — this makes dashboard PnL a *hybrid* (Flex value − derived cost), which will
   not exactly equal IBKR's `fifoPnlUnrealized`. That is an accepted, documented choice;
   `fifoPnlUnrealized` is retained only as a future reconciliation check, not displayed.

### Important note on PnL (document this in code comments)

Dashboard unrealized PnL = **Flex market value − derived cost basis**. Because value now comes
from Flex but cost still comes from our derived lots, this is a hybrid figure. It will land
near IBKR's `fifoPnlUnrealized` when our tracked cost matches IBKR's `costBasisPrice`, but any
cost-basis drift shows up as a PnL difference. This is intentional: it keeps IBKR and CIBC
computing PnL the same way (value − cost), rather than adopting IBKR's PnL for one source and
deriving it for the other. Reconciling dashboard PnL against `fifoPnlUnrealized` is a useful
future data-integrity check.

---

## Data model

Value is now an ingested fact for Flex accounts, not a computed one. Store it as a
snapshot-aligned observation so the daily history chart can read it later.

### `app/repository/db.py` — bump `SCHEMA_VERSION` and add a table

Add `position_values` (Flex-reported values, append-only, snapshot-aligned):

```sql
CREATE TABLE IF NOT EXISTS position_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    value_native REAL NOT NULL,          -- positionValue
    value_base REAL NOT NULL,            -- positionValueInBase (CAD)
    native_currency TEXT NOT NULL,
    fx_rate_to_base REAL,                -- fxRateToBase (provenance)
    quantity REAL NOT NULL,              -- Flex 'position' (for recon vs derived qty)
    source TEXT NOT NULL,                -- 'ibkr_flex_<login>'
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (snapshot_date, account_id, instrument_id)
);
```

Add `"position_values"` to the `TABLES` drop list. Bump `SCHEMA_VERSION` (whatever it is now
→ next integer). This wipes the local dev DB on next run — expected.

> **Why a table, not reuse `prices`:** `prices` is per-instrument (account-agnostic) and
> price-shaped. Flex value is per-(account, instrument) and value-shaped (native + base + fx),
> with no separable price. Keeping it distinct preserves the clean observation semantics.

---

## Changes by layer

### 1. Ingestion — `app/ingestion/ibkr_flex.py`

- Extend `parse_flex_positions` (or add `parse_flex_position_values`) to capture, per
  `OpenPosition`: `positionValue`, `positionValueInBase`, `fxRateToBase`, `currency`,
  `position`, plus existing symbol/conid/cost. Read values **directly**; never reconstruct
  from `markPrice × position`.
- Keep filtering to supported asset classes (EQUITY/ETF). Skip rows with empty
  `positionValue` (log/skip, don't crash).
- Add a `ParsedPositionValue` dataclass to `app/models.py` (account_external_id,
  account_label→accountId fallback, symbol, conid, currency, value_native, value_base,
  fx_rate_to_base, quantity).

### 2. Repository — new `app/repository/position_values.py`

- `upsert_position_values(conn, parsed_values, snapshot_date, source)`: resolve account +
  instrument (reuse `upsert_account`/`upsert_instrument`/`upsert_alias`), upsert one
  `position_values` row per (snapshot, account, instrument). Return count.
- `latest_position_values(conn)`: latest snapshot per (account, instrument), joined to
  accounts + instruments — returns account_label, symbol, name, asset_class, native_currency,
  value_native, value_base, quantity.

### 3. Repository — `app/repository/portfolio.py`

- `latest_position_marks` currently LEFT JOINs `prices`. Change the valuation source to be
  **account-type-aware**:
  - **Flex-sourced accounts** (broker = IBKR): LEFT JOIN `position_values` and expose
    `value_native`, `value_base`, and Flex `quantity`.
  - **Non-Flex accounts** (CIBC): keep the existing `prices` LEFT JOIN path (qty × price).
  - Simplest implementation: return both a `flex_value_base` / `flex_value_native` column
    (from `position_values`) and the existing `price` column; let the service decide which to
    use per row based on presence of a Flex value. (A Flex-sourced row has `value_base`
    populated; a CIBC row has `price` populated.)

### 4. Service — `app/services/portfolio.py`

- In `_holding`, choose the valuation path per row:
  - **If a Flex value is present** (`value_base` not null): `market_value` = `value_native`,
    `market_value_cad` = `value_base` (IBKR's own CAD — do NOT re-apply `to_cad`). `currency`
    = the Flex native currency.
  - **Else (CIBC / price path):** existing behavior — `market_value = quantity × price`,
    `market_value_cad = to_cad(market_value, currency, usdcad)`.
  - **PnL (both paths):** `unrealized_pnl = market_value − (quantity × avg_cost)` in native
    currency (i.e. value − cost). For the Flex path this uses Flex value with derived cost
    (the documented hybrid). Keep `stale_reason` handling: Flex row with no value → "missing
    Flex value"; CIBC row with no price → "missing price".
- `_consolidate` and account-summary math: unchanged in structure, but note IBKR CAD now comes
  from summed `value_base` (already CAD) while CIBC CAD comes from `to_cad(...)`. Both feed
  `market_value_cad`, so summation still works — just confirm no double-FX on IBKR rows.

### 5. Route — `app/routes/dashboard.py`

- In `refresh_transactions`, per login: after parsing transactions/positions/cash, also parse
  position values and call `upsert_position_values(conn, parsed_values, snapshot_date,
  source)`. Fold count into the run status message.
- Gateway price refresh (`/refresh/prices`) stays, but its scope is now **CIBC/non-Flex
  instruments only**. Update `instruments_for_price_refresh` (in
  `app/repository/observations.py`) to select only instruments belonging to non-Flex accounts
  (or lacking any `position_values` row) — so the gateway is never invoked for IBKR holdings.
  (If per-account instrument scoping is awkward, at minimum document that prices are only
  needed for CIBC and it's fine for IBKR instruments to have no price row.)

### 6. Service — `app/services/valuation.py`

- No shape change required if `market_value_cad` continues to carry the right number per row.
  Confirm `positions_total_cad` now equals summed IBKR `value_base` + CIBC `qty×price×fx`.
  `total_cad = positions_total_cad + cash.cash_total_cad` stays correct (cash signed).

### 7. UI — `templates/dashboard.html`

- Consolidated Holdings / Account Drilldown: value columns now populated from Flex for IBKR
  rows. Add a small provenance indicator if easy (e.g. value source "IBKR" vs "market price")
  — optional.
- Confirm the "Positions CAD" stat reflects the Flex-based total and reconciles with what
  IBKR shows when logged in.
- Negative USD cash already handled by the cash section (from the prior refactor) — no change.

---

## Tests

### `tests/test_flex.py`
- Parse `positionValue` / `positionValueInBase` / `fxRateToBase` from an `OpenPosition`
  fixture; assert values read directly and **not** derived from `markPrice` (fixture has empty
  `markPrice`, populated `positionValue`).
- Assert summed `positionValueInBase` for the fixture equals the expected CAD positions total.
- Fixture: model on `testing_v3_0723.xml` — USD positions, empty `markPrice`, populated
  `positionValue`/`positionValueInBase`, `fxRateToBase=1.4087`, empty `acctAlias`.

### `tests/test_repository_and_valuation.py`
- `upsert_position_values` + `latest_position_values`: round-trip per (account, instrument),
  latest snapshot wins.
- **IBKR valuation path:** a holding with a Flex `position_values` row values from
  `value_native` (native) and `value_base` (CAD) — assert CAD total does **not** apply an
  extra FX multiply (regression guard against double-conversion).
- **CIBC valuation path:** a holding with no Flex value but a `prices` row still values via
  `qty × price × fx` (existing behavior preserved).
- **PnL hybrid:** Flex value − derived cost. Set Flex value and a known avg_cost; assert
  `unrealized_pnl = value_native − qty × avg_cost`.
- **Grand total:** positions (IBKR base + CIBC converted) + signed cash = `total_cad`, with a
  negative USD cash reducing the total.
- Update any existing test that assumed `market_value = qty × price` for IBKR rows.

---

## Verification

- `python -m pytest -q` — all green.
- Manual: reset dev DB, refresh transactions against a Flex query with Open Positions enabled.
  Confirm:
  - Consolidated Holdings values match IBKR's per-position values,
  - Positions CAD ≈ sum of `positionValueInBase` (matches IBKR's account value),
  - IBKR holdings show values even with the gateway **down** (no price fetch needed),
  - CIBC holdings still require a gateway price (or show "missing price" until seeded),
  - Total CAD = positions + cash, with negative USD cash reducing it.

---

## Notes / future

- **Daily value history now has a clean source.** Each EOD Flex run writes a
  `position_values` snapshot per (date, account, instrument). Summed per date = portfolio
  value time series for IBKR — feeds the value-vs-contributions chart's value line, and it's
  fully hands-off (no gateway). CIBC value on a given day still needs a price that day; given
  CIBC is near-static, a stale/seed price is acceptable (document the assumption).
- **`fifoPnlUnrealized` as a recon check.** Store it later if you want a
  Flex-PnL-vs-derived-PnL integrity check; not displayed now.
- **Multi-account / two logins:** `position_values` keys on (account, instrument), so multiple
  IBKR statements aggregate naturally.

## Out of scope

- Adopting IBKR's `fifoPnlUnrealized` for display (kept derived).
- Removing the gateway entirely (retained for CIBC).
- The EOD scheduler itself (separate work item, now unblocked for IBKR).
