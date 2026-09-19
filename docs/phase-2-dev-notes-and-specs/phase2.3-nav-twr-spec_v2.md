# Dev Spec — Phase 2.3: NAV Integration, TWR, Return Metrics, Portfolio Weights (DATA MODEL ONLY)

Scope: **data model only.** Parse the new NAV sections, store daily NAV, compute/consume TWR,
add return + weight attributes to the service layer. UI rendering is a **separate sprint** —
this spec only ensures the backend data points exist and are correct. Verified against
`NAV_testing_v2.xml` (account U24081754).

---

## Source data findings (verified)

The NAV-enabled Flex query is **additive** — existing trades/positions/cash sections are
unchanged; two new nodes appear:

1. **`ChangeInNAV`** (1 per account, YTD summary) — carries IBKR's **pre-computed TWR**.
   Sample: `twr=10.090499676`, `currency=CAD`, `fromDate=20260101 toDate=20260904`,
   `endingValue=33044.502412988`, `depositsWithdrawals=30000`, `dividends=100.6`, etc.
   → **per-account TWR: direct consume, no computation.**

2. **`EquitySummaryByReportDateInBase`** (178 rows, one per business day, `reportDate`) — the
   **daily NAV series**, back to `20251231`. → store for the growth chart + consolidated TWR.

### Corrections to the requirements doc (important)
- The daily node is **`EquitySummaryByReportDateInBase`**, NOT `EquitySummaryInBase` (a single
  node by the shorter name also exists but is not the daily series). Target the `ByReportDate`
  variant to get all 178 daily rows.
- **Use the `total` field directly as daily NAV.** (The earlier NAV-only test file lacked it;
  the updated Flex query now includes `total`.) `total` is IBKR's authoritative daily NAV and
  reconciles to `ChangeInNAV.endingValue` to the cent across all rows.
  **Do NOT hand-sum components.** `total = cash + stock + dividendAccruals + interestAccruals`
  (and potentially other accrual components). The earlier spec's `cash + stock +
  dividendAccruals` formula is WRONG — it omits `interestAccruals`, which is zero for cash
  accounts (RRSP) but non-zero for margin accounts (Corporate), so hand-summing silently
  under/over-states NAV by the missing accrual. Verified: for the Corporate account,
  `cash+stock+div` was off by 2.12 (the interestAccruals), while `total` matched exactly.
- **23 leading rows have NAV = 0** (pre-funding; first non-zero is 20260202 = 5000). TWR math
  must skip the pre-funding period (see TWR section).
- `ChangeInNAV.twr` is **CAD, YTD, per-account**.

---

## Part 1 — Parse the NAV sections

`app/ingestion/ibkr_flex.py`:
- `parse_flex_change_in_nav(xml)` → per account: `account_external_id`, `acct_alias`,
  `currency`, `from_date`, `to_date`, `twr` (float), plus the component fields
  (`starting_value`, `ending_value`, `deposits_withdrawals`, `dividends`, `mtm`, ...) — keep
  the full set; cheap and useful later.
- `parse_flex_daily_nav(xml)` → list of per-day rows: `account_external_id`, `report_date`
  (normalize `20251231`→`2025-12-31`), and **`nav` read directly from the `total` attribute**
  (IBKR-authoritative). Optionally also retain `cash`, `stock`, `dividend_accruals`,
  `interest_accruals` as component columns for reference, but `nav` = `total`, NOT a hand-sum.
- Both are additive parsers; existing parsing untouched. Add `ParsedChangeInNav` and
  `ParsedDailyNav` dataclasses to `app/models.py`.

---

## Part 2 — Storage (new tables)

`app/repository/db.py` (bump `SCHEMA_VERSION`):

```sql
-- Daily NAV series (backfilled to account inception / Dec 2025). Full history retained.
CREATE TABLE IF NOT EXISTS daily_nav (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    report_date TEXT NOT NULL,
    nav REAL NOT NULL,                 -- IBKR 'total' field (authoritative daily NAV, base ccy CAD)
    cash REAL, stock REAL, dividend_accruals REAL, interest_accruals REAL,
    currency TEXT NOT NULL,
    content_hash TEXT,                 -- provenance link to evidence
    ingested_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, report_date)
);

-- Per-account TWR summary (IBKR-computed, from ChangeInNAV). One current row per account.
CREATE TABLE IF NOT EXISTS nav_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    twr REAL,                          -- IBKR ChangeInNAV.twr (CAD, YTD)
    ending_value REAL,
    deposits_withdrawals REAL,
    dividends REAL,
    currency TEXT NOT NULL,
    content_hash TEXT,
    ingested_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, to_date)
);
```
Add both to the `TABLES` drop list.

**Backfill behavior:** `daily_nav` is upserted on `(account_id, report_date)`. Each pull
carries the full YTD series, so re-running self-heals and backfills — the growth chart has
history from day one (back to Dec 2025), solving the forward-only-history problem. Skip storing
rows with `nav = 0` pre-funding, OR store them (harmless, they're real zeros); recommend
**storing them** — they're legitimate pre-funding NAV and simplify the series. TWR math skips
them, not storage.

`app/repository/nav.py` (new): `upsert_daily_nav`, `upsert_nav_summary`, `latest_nav_summary`,
`daily_nav_series(account_id=None)` (None = all accounts, for consolidation).

---

## Part 3 — Ingest wiring

`app/routes/dashboard.py` (`_ingest_flex_xml`, inside the existing transaction):
- Parse both NAV nodes; `upsert_daily_nav` and `upsert_nav_summary`, passing the run's
  `content_hash` / `ingested_at` for provenance.
- Additive to the existing flow; does not affect the PnL-ready gating (NAV is independent of
  the position PnL fields).

---

## Part 4 — TWR (per-account consume; consolidated compute)

### Per-account TWR — DIRECT CONSUME
Read `nav_summary.twr` (IBKR's `ChangeInNAV.twr`). No computation. This is authoritative.

### Consolidated TWR — COMPUTE from the daily NAV series
Cannot be derived by averaging per-account TWRs (TWRs don't aggregate linearly). Compute from
the **summed daily NAV across accounts** + dated cash flows:

1. Build a combined daily series: for each `report_date`, `combined_nav = Σ account nav`.
2. Get dated daily net external cash flows (deposits − withdrawals) from the contributions
   data (`transactions` DEPOSIT/WITHDRAWAL by date), summed across accounts, converted to the
   NAV's base currency (CAD).
3. **Daily-linked TWR:** for each day with a defined prior NAV, sub-period return
   `R_t = (NAV_t − CF_t) / NAV_{t-1} − 1` (standard: remove the day's external flow from the
   end NAV before comparing to prior NAV; adjust sign convention per how flows are timed — see
   below). Then `TWR = Π(1 + R_t) − 1`.
4. **Skip the pre-funding period:** the first 23 rows have NAV = 0. Start linking from the
   first day where `NAV_{t-1} > 0` (avoid divide-by-zero). Handle the funding day itself
   correctly (the day NAV goes 0 → funded is a pure cash flow, ~0 return, not an infinite one).

### VALIDATION (this is the correctness guarantee — do this)
Because IBKR gives per-account TWR authoritatively, **validate the daily-NAV TWR computation
against IBKR's per-account figure first.** Run the same daily-linked algorithm on a single
account's daily NAV + that account's cash flows, and confirm it reproduces
`ChangeInNAV.twr` (10.0905 for U24081754) to within a few basis points. Only once the algorithm
reproduces IBKR's per-account TWR should the consolidated result be trusted. Add this as a test
(below). This neutralizes the main risk of computing TWR ourselves.

### Cash-flow timing convention (the subtle part)
Daily NAV is end-of-day. A deposit on day T is reflected in NAV_T. So the day's investment
return is `(NAV_T − flow_T) / NAV_{T-1} − 1` (end NAV minus the flow that arrived that day,
over prior end NAV). Verify this convention *is* the one that reproduces IBKR's TWR during
validation; if IBKR times flows start-of-day, adjust to `NAV_T / (NAV_{T-1} + flow_T) − 1`.
The validation step tells you which convention IBKR uses — use whichever reproduces 10.0905.

`app/services/nav.py` (new): `get_account_twr(account_id)` (consume), `compute_twr(nav_series,
cash_flows)` (the linked algorithm), `get_consolidated_twr()` (combined series → compute).

---

## Part 5 — % return + weight attributes (service layer)

1. **Per-position USD return** (Account Drilldown): `(value_usd − cost_basis_usd) /
   cost_basis_usd`. Both already available. Add `pct_return_usd` to `HoldingRow`. Guard
   divide-by-zero → None.
2. **Consolidated per-symbol weighted-average return**: aggregate from positions
   (Σ value − Σ cost) / Σ cost per symbol. Add to the consolidated holding rows.
3. **Top-level cards (two returns):**
   - **Simple Return** = `(total_cad_incl_cash − contributions_cad) / contributions_cad`
     (already have inputs). Label precisely "Simple Return."
   - **Time-Weighted Return** = consolidated TWR from Part 4. Label "Time-Weighted Return."
   - These differ by design (Simple is contribution-timing-sensitive; TWR removes timing) —
     the gap is information, not error. (UI labeling is next sprint; data-model exposes both.)
4. **Stock % weight** (for the future pie charts): per position, two weights —
   `weight_by_value = position_value_cad / total_positions_value_cad` and
   `weight_by_cost = position_cost_basis_cad / total_cost_basis_cad`. Add both to the holding
   rows. No new source data. (Pie rendering is next sprint.)

Add the new fields to `DashboardData` / the relevant dataclasses so the (future) UI can read
them. Per Phase 2.3 framing, **no UI changes in this spec** — only the data-model/service
layer.

---

## Tests
- **Parse:** `parse_flex_change_in_nav` reads `twr` and dates; `parse_flex_daily_nav` reads
  `nav` from the `total` attribute directly. Assert `nav == total` (NOT a hand-sum) and that the
  last row's `nav` == `ChangeInNAV.endingValue` to the cent. Regression guard: on a margin
  account (non-zero interestAccruals), confirm `nav` uses `total`, not `cash+stock+div` (which
  would be off by the interest accrual).
- **Daily NAV backfill:** upsert 178 rows; re-ingest same file → still 178 (dedup by
  account+report_date), values unchanged.
- **TWR validation (critical):** `compute_twr` on U24081754's daily NAV + cash flows reproduces
  IBKR `ChangeInNAV.twr = 10.0905` within a few bps. (Regression guard on the whole TWR
  approach.)
- **Pre-funding handling:** the 23 leading zero-NAV rows don't cause divide-by-zero; TWR starts
  from first funded day.
- **Consolidated TWR:** with a second account's daily NAV, computes a combined TWR (sanity: not
  equal to a naive NAV-weighted average of per-account TWRs).
- **% return:** per-position USD return matches `(value−cost)/cost`; zero-cost → None.
- **Weights:** Σ weight_by_value = 1.0 (±rounding); Σ weight_by_cost = 1.0.
- Existing suite still green (NAV parsing is additive).

## Verification
- Ingest NAV-enabled Flex: `daily_nav` has the full backfilled series per account;
  `nav_summary` has per-account TWR; consolidated TWR computed and validated against IBKR
  per-account; per-position returns and weights present on the holding rows. No UI change yet.

## Notes / next sprint (UI, out of scope here)
- Growth chart from `daily_nav` (value line now backfilled to Dec 2025 — no empty-start
  problem). Contribution line overlaid from dated deposits.
- Two return cards (Simple + TWR), per-account TWR in Accounts section, per-position return in
  Drilldown, two pie charts (weight by value / by cost). All read the fields this spec adds.
- Currency note: TWR and daily NAV are CAD (base). Consistent with the CAD PnL side of the
  valuation model.
