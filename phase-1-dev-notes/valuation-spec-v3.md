# Dev Spec — Valuation v3: Unified Total + Batch (USD from trades, CAD from Flex)

## Goal

Make the dashboard mirror IBKR's UI exactly, with two PnL lenses. Supersedes valuation v1/v2.
The core correction from v2: **USD cost basis comes from summing trade-level `cost`, not from
`costBasisPrice / fxRateToBase`** (which was an FX-timing approximation). This makes batch and
total foot exactly and matches IBKR's UI cost basis / avg price to the cent.

All verified against `testing_v3_0801.xml` (account U24872141) and the SOFI IBKR UI screenshots.

---

## Two-sided lineage (the mental model)

- **USD side** (avg cost, cost basis, USD price PnL): **derived from trades/lots** — exact,
  and batch-sums-to-total by construction.
- **CAD side** (position value CAD, CAD PnL): **read from Flex `OpenPosition`** — IBKR
  authoritative.
- **Shared** (position value USD, market price): from Flex `OpenPosition`.

Currency rule: compute USD-native. Convert to CAD only for (a) cash balances and (b) overall
portfolio total, using **current market FX**. Per-position CAD value is NOT a conversion — it
is read directly from `positionValueInBase`.

---

## TOTAL POSITION — data lineage

| Field | Source | Formula | Ccy |
|---|---|---|---|
| Position | `OpenPosition.position` | direct | — |
| Market Price | `OpenPosition.markPrice` | direct (mark, not "last") | USD |
| Position Value (USD) | `OpenPosition.positionValue` | direct (= markPrice × position) | USD |
| **Avg cost / unit (USD)** | **trades** | **Σ trade.cost / Σ trade.qty** | USD |
| **Position Cost Basis (USD)** | **trades** | **Σ trade.cost** (BUY lots) | USD |
| **USD PnL** | derived | `positionValue − Σ trade.cost` | USD |
| **CAD PnL** | `OpenPosition.fifoPnlUnrealized` | direct | CAD |
| Position Value (CAD) | `OpenPosition.positionValueInBase` | direct | CAD |

**Dropped:** `costBasisPrice / fxRateToBase`. No longer used for USD cost (it was ~$25 off on
SOFI, up to ~$77 on QQQ). USD cost now = summed trade `cost`.

Verified identities (SOFI):
- `positionValue` = `markPrice × position` = 1957.20 ✓ (both = IBKR UI market value)
- Σ trade.cost = 1595.93 + 321.70 = **1917.63** ✓ = IBKR UI Total Cost Basis
- Σcost/Σqty = 1917.63/120 = **15.98** ✓ = IBKR UI avg price
- `positionValueInBase` = 2743.41 ✓ = `costBasisPrice×qty + fifoPnl` (IBKR identity)
- `fifoPnlUnrealized` = 90.05 (CAD, native — no conversion needed)

---

## BATCH (per lot) — data lineage

Granularity: **same-day, same-symbol buys merge into one lot** (weighted average). This is the
current `rebuild_lots` behavior (test `test_same_day_buys_merge_into_one_weighted_average_batch`
already asserts it) — no lot-logic change needed. Matches IBKR's per-acquisition-date rows.

| Field | Source | Formula | Ccy |
|---|---|---|---|
| Acquired date | lot `open_date` (from `tradeDate`) | direct | — |
| Quantity | lot `remaining_qty` | direct | — |
| **Avg cost / unit (USD)** | lot | **lot cost / lot qty** | USD |
| **Batch cost (USD)** | trades | **trade `cost`** (Flex; already = tradePrice×qty + \|commission\|) | USD |
| Market Price | `OpenPosition.markPrice` | borrowed (same mark for all lots of the symbol) | USD |
| **Batch Value (USD)** | derived | `quantity × markPrice` | USD |
| **Batch PnL (USD)** | derived | `Batch Value − Batch cost` | USD |

### CRITICAL parser fix — commission sign

The proposed formula `tradePrice + ibCommission/quantity` is **wrong**: `ibCommission` is
**negative** in Flex (e.g. −1.0003), so adding it *understates* cost. Two correct options:
- **Preferred:** don't compute — use Flex trade **`cost`** directly (already includes
  commission). SOFI lots: `cost` = 1595.9303 and 321.70006 → match IBKR UI exactly.
- If avg cost/unit is needed: **`cost / quantity`** (not the commission formula). SOFI:
  1595.93/100 = 15.9593 ≈ IBKR 15.96; 321.70/20 = 16.085 ≈ IBKR 16.09. ✓

Do **not** use `tradePrice + ibCommission/quantity`. If commission must be added manually,
it is `tradePrice + |ibCommission|/quantity` — but `cost` already does this, so prefer `cost`.

### Batch-sums-to-total (holds by construction)

- Σ batch qty = Position ✓ (existing reconciliation)
- Σ batch cost (Σ trade.cost) = Total USD cost basis **by definition** (total now = sum of
  batch) ✓
- Σ batch value (Σ qty × markPrice) = `positionValue` ✓ (same mark, qty foots)
- Σ batch USD PnL = Total USD PnL ✓

This is why total USD cost basis is *defined as* the sum of batch costs — the identity is
structural, not coincidental.

---

## Two PnL columns (keep both — they decompose, don't duplicate)

1. **USD PnL** (derived): `positionValue − Σ trade.cost`. Pure USD price/capital gain. SOFI
   +64.24 USD (position level).
2. **CAD PnL** (Flex): `fifoPnlUnrealized`. Real-life, FX-inclusive, CAD-native. SOFI +90.05.

Relationship (document in comments, do NOT try to reconcile the wrong pair):
- USD PnL × current FX ≈ *total* CAD gain, **not** IBKR's `unrealizedCapitalGainsPnl`.
- IBKR splits its CAD PnL as `unrealizedCapitalGainsPnl + unrealizedlFxPnl = fifoPnlUnrealized`
  (verified to cent). That split uses a different cost-FX basis than our USD×spot approach.
- So: our USD PnL and IBKR's cap-gains component are two valid but non-identical windows on
  "price gain." Keeping USD PnL + CAD `fifoPnl` enables future attribution; storing
  `unrealizedCapitalGainsPnl`/`unrealizedlFxPnl` (optional) enables exact IBKR-style
  attribution later.

Note: IBKR UI "Unrealized P&L" column = `fifoPnlUnrealized` (CAD). Our USD PnL will NOT equal
that column — by design.

---

## Changes by layer

### 1. Ingestion — `app/ingestion/ibkr_flex.py`
- Trades parse: ensure **`cost`** and **`ibCommission`** are captured on each `Trade`
  (prefer `cost`). `cost` already includes commission and matches IBKR lot cost basis.
- OpenPosition parse: capture `markPrice`, `positionValue`, `positionValueInBase`,
  `fxRateToBase`, `fifoPnlUnrealized`, `position`, `costBasisPrice` (keep `costBasisPrice`
  only for the CAD identity/recon, NOT for USD cost). Optionally
  `unrealizedCapitalGainsPnl`, `unrealizedlFxPnl` for future attribution.
- Add fields to `ParsedTrade`/`ParsedPositionValue` dataclasses as needed.

### 2. Repository — lots (`app/repository/positions.py`)
- `rebuild_lots` already merges same-day same-symbol buys (weighted avg) — keep as is.
- Ensure each lot stores its **`cost`** basis from the trade `cost` (sum of merged trades'
  `cost`), so `avg cost = lot.cost / lot.qty` and batch cost = `lot.cost` directly.
  (If lots currently store `cost_per_unit` from `tradePrice`, switch the source to trade
  `cost` so commission is included and it matches IBKR.)

### 3. Repository — `position_values` / portfolio reads
- Total USD cost basis / avg cost now come from **lots** (Σ cost, Σ cost/Σ qty), not from
  `position_values.costBasisPrice`. Provide a read that joins OpenPosition (for value/mark/CAD)
  with aggregated lot cost (for USD cost).
- Keep `positionValueInBase` and `fifoPnlUnrealized` from `position_values` for the CAD columns.

### 4. Service — `app/services/portfolio.py` and `batch_pnl.py`
- **Total (`_holding`)**, IBKR rows:
  - value USD = `positionValue`; value CAD = `positionValueInBase` (no `to_cad`)
  - price = `markPrice`
  - USD cost basis = Σ lot cost; avg cost = Σ lot cost / qty
  - USD PnL = `positionValue − Σ lot cost`
  - CAD PnL = `fifoPnlUnrealized`
- **Batch (`get_batch_pnl`)**:
  - batch cost = lot cost (USD); avg cost = lot cost / qty
  - batch value = lot qty × `markPrice` (borrow the symbol's mark from OpenPosition)
  - batch USD PnL = batch value − batch cost
  - (batch CAD PnL optional/future; IBKR only gives `fifoPnl` at position level, so CAD PnL
    per lot would need allocation — defer.)
- **CIBC** unchanged: derived qty × gateway price × FX; derived-cost PnL.

### 5. UI — `templates/dashboard.html`
- Holdings/total: show Market Price (markPrice), Position Value (USD), Position Value (CAD),
  Avg Cost (USD, from lots), USD PnL, CAD PnL.
- Batch section: per lot — Acquired, Qty, Avg Cost (USD), Batch Cost (USD), Market Price,
  Batch Value (USD), USD PnL. Confirm batch totals foot to the position row.
- Flag the two-PnL meaning briefly (USD = price gain; CAD = incl. FX / mirrors IBKR).

---

## Tests
- Trade parse: `cost` captured; SOFI lots cost = 1595.93 / 321.70; commission is negative and
  NOT added with the wrong sign.
- Avg cost: `cost/qty` → 15.96 / 16.09 per lot; total 15.98 (regression guard for the sign bug
  and the costBasisPrice/fx drop).
- Total USD cost basis = Σ lot cost = 1917.63 (NOT 1892.96 from costBasisPrice/fx).
- Batch-sums-to-total: Σ batch cost == total USD cost basis; Σ batch value == positionValue;
  Σ batch USD PnL == total USD PnL.
- CAD columns exact from Flex: position CAD = `positionValueInBase` (2743.41);
  CAD PnL = `fifoPnlUnrealized` (90.05).
- Identity checks: `positionValue == markPrice × position`;
  `positionValueInBase == costBasisPrice×qty + fifoPnlUnrealized`.

---

## Verification
- `python -m pytest -q` — green.
- Manual vs IBKR UI (SOFI): avg price 15.98, cost basis 1917.63, market value 1957.20, position
  value CAD 2743.41, CAD PnL 90.05; per-lot 15.96/16.09 and 1595.93/321.70. All should match.

## Notes / future
- **Approximation eliminated:** the `costBasisPrice/fxRateToBase` avg-cost approximation is
  gone; USD cost is now exact from trades. No UI caveat needed anymore.
- **PnL attribution:** storing `unrealizedCapitalGainsPnl` + `unrealizedlFxPnl` enables an
  exact IBKR-style capital-vs-FX split later.
- **Per-lot CAD PnL** deferred (Flex gives CAD PnL only at position level).
- **Multi-account / CIBC:** CIBC keeps derived qty × gateway price; batch cost for CIBC comes
  from its CSV trade rows the same way (cost or price×qty + fees).
