# Phase 1 Architecture & Decisions

**Status:** Phase 1 complete — the ingestion/infrastructure layer is functional and produces
IBKR-accurate valuations across multiple accounts. This document consolidates the *final state
and locked decisions*, not the debugging history. It is the reference for extending the
platform in later phases.

**Milestone numbers (two-account reference, `testing_v3__1_.xml` + `rrsp_v1_based_ccy.xml`):**
positions CAD 67,185.21 + net cash −985.65 = **total CAD 66,199.55**; 0 reconciliation
warnings; 39 tests passing.

---

## 1. What Phase 1 delivers

A local-first dashboard that consolidates holdings across multiple IBKR accounts (two logins)
plus CIBC, normalized to CAD, mirroring what IBKR's own UI shows. Core features:

- Multi-account ingestion of trades, cash flows, positions, and cash balances via IBKR Flex.
- Canonical instrument identity with cross-account consolidation of shared symbols.
- Valuation that mirrors IBKR: position value, cost basis, per-unit cost, market price.
- Dual unrealized PnL: USD price PnL (derived) and CAD total PnL (`fifoPnlUnrealized`).
- Batch (lot-level) PnL that sums exactly to the position total.
- Cash section per account/currency with a Net Cash CAD figure feeding the grand total.
- Dated contributions series.
- Position reconciliation (derived qty vs broker qty) as a data-integrity check.
- Refresh hardening against IBKR rate-limiting (inter-login delay, cooldown, throttle-aware
  errors).

---

## 2. Architecture (three layers, downward-only dependencies)

```
routes/      thin FastAPI handlers — no logic, no SQL
services/    business logic, the internal API — NO SQL
repository/  ALL SQL lives here
ingestion/   external world → normalized dataclasses → repository writes
```

The dashboard is the first consumer of the service layer. The separation held through every
Phase 1 change — new features slotted into the existing layers without restructuring, which is
the practical proof the foundation is sound.

Key modules:
- `ingestion/ibkr_flex.py` — Flex parsing (trades, cash txns, open positions, cash reports).
- `ingestion/cibc_csv.py` — CIBC transaction CSV parser.
- `ingestion/ibkr_gateway.py` / `services/pricing.py` — gateway pricing (CIBC only; see §7).
- `repository/` — `transactions`, `positions` (lots + derived snapshots + reconciliation),
  `position_values`, `cash`, `observations` (prices/fx), `instruments`, `runs`.
- `services/` — `portfolio`, `batch_pnl`, `cash`, `growth`, `valuation` (orchestrator →
  `DashboardData`), `instruments`.

---

## 3. Data model (SQLite, schema v7)

Tables: `accounts`, `instruments`, `instrument_aliases`, `transactions`, `lots`, `positions`,
`prices`, `position_values`, `fx_rates`, `reconciliations`, `cash_balances`, `ingestion_runs`.

Roles and persistence behavior:

| Table | Role | On refresh |
|---|---|---|
| `transactions` | **Source of truth** (immutable ledger) | append-only, deduped on `(source, external_id)` |
| `lots` | Open lots derived from BUY/SELL | fully rebuilt each ingest (pure derivation) |
| `positions` | Daily derived snapshots | overwritten within a `snapshot_date`, preserved across dates |
| `position_values` | Flex-reported value/cost/mark per position | upserted per `(snapshot_date, account, instrument)` |
| `cash_balances` | CashReport `endingCash` per account/currency | upserted per snapshot |
| `prices` / `fx_rates` | Observation pattern (gateway/manual) | append-only by `as_of` |
| `reconciliations` | Derived-qty vs broker-qty check | append-only |
| `instruments` / `accounts` / `instrument_aliases` | Canonical identity | upserted in place |
| `ingestion_runs` | Refresh status/audit | append-only |

**Note:** a `SCHEMA_VERSION` bump drops and rebuilds all app tables (dev-time behavior).
The local SQLite file is not yet a durable archive that survives schema changes — source data
is re-fetched from Flex. (Deferred hardening item.)

**Removed in Phase 1:** `cash_reconciliations` (cash recon dropped — see §6).

---

## 4. Valuation lineage (mirror IBKR) — LOCKED

Compute in USD natively; convert to CAD only where noted. Verified to the cent against IBKR's
UI (SOFI: avg 15.98, cost basis 1917.63, value 1957.20 USD / 2743.41 CAD, USD PnL 39.57,
CAD PnL 90.05).

### Total Position
| Field | Source | Formula | Ccy |
|---|---|---|---|
| Position | `OpenPosition.position` | direct | — |
| Market Price | `OpenPosition.markPrice` | direct (mark, not "last") | USD |
| Position Value | `OpenPosition.positionValue` | direct (= markPrice × qty) | USD |
| Avg cost / unit | trades | Σ trade.cost / Σ qty | USD |
| Position Cost Basis | trades | Σ trade.cost (BUY lots) | USD |
| **USD PnL** | derived | positionValue − Σ trade.cost | USD |
| **CAD PnL** | `OpenPosition.fifoPnlUnrealized` | direct | CAD |
| Position Value (CAD) | `OpenPosition.positionValueInBase` | direct | CAD |

### Batch (per lot)
Same-day same-symbol buys merge into one lot (weighted average). Cost from trade `cost`
(already includes commission — do **not** use `tradePrice + ibCommission/qty`; commission is
negative in Flex). Batch value = qty × `markPrice`. Batch USD PnL = value − cost.
**Batch sums to total by construction:** total USD cost basis is *defined as* the sum of batch
costs.

### Locked decisions & rationale
- **Value/cost/CAD-PnL come from Flex** (IBKR authoritative); USD cost basis is derived from
  summed trade `cost` (exact; replaced the earlier `costBasisPrice/fxRateToBase` approximation
  which drifted up to ~$77/position).
- **`markPrice × position` reconciles to `positionValue`** — `positionValue` stays
  authoritative for totals; `markPrice` is per-unit display + cross-check.
- **Two PnLs are a decomposition, not duplication:** USD PnL = pure price gain; CAD PnL =
  `fifoPnlUnrealized` (FX-inclusive, real-life). They are *not* inter-convertible (USD × spot
  ≠ CAD PnL); IBKR further splits CAD PnL into `unrealizedCapitalGainsPnl` +
  `unrealizedlFxPnl` — stored for future attribution, not displayed.
- **Derived cost/qty is reconciliation-only for IBKR** — the transaction ledger drives lots,
  batch PnL, and the qty reconciliation check, but not the main IBKR valuation line.

---

## 5. Cash (LOCKED)

- **Source of truth: `CashReport.endingCash`** per account + currency (signed; USD can be a
  negative margin debit).
- **Net Cash CAD** = `CAD balance + USD balance × fxRateToBase`, where `fxRateToBase` is the
  **portfolio-wide most-recent** rate from `position_values` (it's a per-statement snapshot,
  identical across accounts on a given date).
- **Fallback:** if no account anywhere has a USD position (no `fxRateToBase`) but USD cash
  exists → Net Cash CAD shows "needs FX" and is excluded from the total.
- **Grand total** = `positions_total_cad + Σ net_cash_cad` (signed) → negative margin cash
  correctly reduces the headline.
- This sources FX from Flex position data, so cash conversion does **not** depend on the
  gateway `fx_rates` table.

---

## 6. Reconciliation (LOCKED)

- **Position reconciliation kept:** derived qty (from transactions) vs broker qty (from Flex
  positions), per account/instrument. A nonzero difference usually means the Flex date range
  doesn't cover full history.
- **Cash reconciliation dropped entirely.** Since `endingCash` is authoritative, there was no
  independent "derived cash" to check against — the old balance check compared IBKR's truth to
  a broken deposit-sum and only produced false alarms. Table and code removed.

---

## 7. Pricing & FX (LOCKED)

- **IBKR positions:** never priced via the gateway — value/mark come from Flex. So IBKR
  valuation is fully hands-off (stateless Flex token, no live session).
- **Gateway scope:** retained *only* for CIBC/non-Flex accounts, which have no Flex-reported
  value and need an external price. Low-volume, seeded rarely.
- **FX for cash:** from Flex `fxRateToBase` (§5). The gateway/manual USDCAD path remains for
  CIBC pricing but no longer gates the cash display or the total.

---

## 8. Refresh hardening (LOCKED)

IBKR Flex is aggressively rate-limited. Refresh logic:
- **Inter-login delay** (`flex_inter_login_delay_seconds`, default 15s) between logins — the
  root fix for the "second login breaks the first" throttle pattern.
- **Refresh cooldown** (`flex_refresh_cooldown_seconds`, default 60s) — rejects rapid repeat
  clicks that self-inflict throttling.
- **Throttle-aware errors** — 1025/10010 surfaced as "rate-limited, wait and retry"; per-login
  failures isolated (one login failing leaves the other's stored data intact).

### IBKR-side timing (operational, not code)
`ErrorCode=1001 "statement could not be generated"` is a **timing** issue, not a bug: IBKR
regenerates Flex statements overnight/around market open. Pull statements in **stable windows**
— weekday evenings after market close, or weekends. This dictates that the eventual daily
scheduler (Phase-later) must run in the evening, not the morning.

---

## 9. Known limitations / deferred (carry into later phases)

- **Daily value history** requires a scheduler (evening cadence per §8). Contributions are
  backfillable from Flex now; portfolio *value* per day only accumulates forward once
  scheduling starts.
- **CIBC valuation** depends on the gateway for prices (near-static, acceptable stale/seed
  price).
- **Durable data archive** — a schema bump currently wipes local data; source-of-truth
  persistence across schema changes is unbuilt.
- **USDCAD stat / gateway FX** is independent of the cash FX path; may show "missing" without
  affecting cash or the total.
- **Reference-data enrichment** (yfinance) exists behind a pluggable interface but is not
  central to Phase 1 valuation.

---

## 10. Extension seams (why later phases are additive)

- **Observation pattern** (`prices`, `fx_rates`: `instrument_id + as_of + source + value`) is
  reused for any future timestamped fact (sentiment, factor exposures).
- **Instrument identity** is the ecosystem-wide join key — kept portfolio-agnostic so a second
  consumer could reference it without restructuring (extraction deferred until a second
  instrument-minting consumer appears).
- **Service layer** is the internal API; consumers call functions, never SQL. A future REST
  API just wraps it.
- **Pluggable providers** (reference data; pricing) isolate the components most likely to
  change.
