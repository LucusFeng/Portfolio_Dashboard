# Dev Spec — Phase 2.4: Pre-Phase-3 Housekeeping

Scope: UI-layout housekeeping + a contribution-table enrichment, before the Phase 3 analysis
screens. Structured so **service-layer/data work is separable from pure UI work** (per the
data-model-vs-UI split). Each item is tagged **[SERVICE]** (backend data assembly / calcs) or
**[UI]** (layout, navigation, rendering).

---

## 1. Main screen = "the facts" [UI]

The current dashboard becomes the **main introductory screen**, focused on current holdings +
total market value. Analysis features (Phase 3) live on a separate **"Portfolio Analysis"**
surface, reached via navigation buttons from the main screen.

- Main screen shows: holdings, per-account and consolidated market value, the minimal return
  cards (item 3), and — for now — the enriched contribution table (item 2). *(Note: the
  contribution view conceptually belongs to Balance Trend (3.3); it stays on the main screen
  only until 3.3 exists, then moves. Build it so it's a self-contained component that can be
  relocated.)*
- Navigation scaffold for the future "Portfolio Analysis" screens (Performance, Allocation,
  Balance Trend, What-If) — even as stubs — so the main/analysis split is established.
- No new computation here; this is layout + navigation.

---

## 2. Enriched Contribution Table

### 2.1 Filter to contribution-event rows only [SERVICE]
The table currently grows every run (one row per daily NAV report date) — noise. Filter to
**only dates where a contribution occurred**, identified from the **transaction ledger's
DEPOSIT/WITHDRAWAL `txn_date`**.

### 2.2 Final table model (identity: consolidated wealth-over-time, sampled at contributions)
- **One consolidated row per date.** Same-day contributions across multiple accounts collapse
  into a single row; the contribution amount is the **sum across accounts** that day. The table
  is a whole-portfolio view, not per-account.
- **Columns (left→right):**
  | Column | Source / formula | Notes |
  |---|---|---|
  | Date | contribution date (ledger `txn_date`) | "when I contributed" — option (a) |
  | Contribution | cumulative consolidated contributions through that date (CAD) | existing col, now consolidated |
  | **Total Value** *(reference)* | consolidated NAV at (or nearest to) the row's date | from `EquitySummaryByReportDateInBase.total`, summed across accounts; simple nearest-date lookup |
  | **Change In Value** *(reference)* | Total Value − cumulative Contribution | dollar value (CAD) |
  | **Growth** *(reference)* | Change In Value / cumulative Contribution | % — approximate simple return |

- **Row date = `availableForTradingDate`** (see 2.2.1). In the common case this equals the NAV
  `reportDate` where the cash lands, so the row date and its Total Value date coincide — no
  deliberate misalignment (this is simpler than earlier drafts).
- **Total Value is a REFERENCE column, not a precision metric.** Its purpose is a high-level
  "roughly at this point, what was my total wealth?" picture, sampled at contribution events. It
  does NOT need to sync exactly to the contribution — the precise performance math lives in TWR
  (unchanged). So use a **simple nearest-NAV lookup** at the row's date (NAV on that date, else
  nearest available); do NOT build settlement-precision / roll-forward rules. Approximate-but-
  close is the intended standard. Label the Total Value / Change / Growth columns as reference
  (e.g. header "Total Value (ref.)") so their approximate numbers aren't mistaken for the precise
  TWR.

### 2.2.1 Row date = `availableForTradingDate` [SERVICE]
The row's date comes from `CashTransaction.availableForTradingDate`, NOT the ledger `txn_date`.
Rationale (validated across both account types):
- It is when the cash actually becomes usable capital in the account (before that it can be
  reversed/rejected) — the economically correct anchor for a wealth-context view.
- It normalizes across account types: RRSP has a longer hold before cash is tradeable; Corporate
  is near-immediate. Verified: RRSP `availableForTradingDate=20260202` matches the NAV reportDate
  where the cash appears (20260202); Corporate `availableForTradingDate=20260902` matches the NAV
  date where the ~5000 diff appears (20260902). One field, correct for both.
- **Fallback chain when empty:** `availableForTradingDate` → `reportDate` → `settleDate` →
  ledger `txn_date`. (It can be blank on some transaction types; never drop a contribution row —
  fall back.)

> This does NOT change TWR. TWR keeps its `reportDate`-aligned flows (2.31, validated to reproduce
> IBKR's 14.11 / 28.70). `availableForTradingDate` and `reportDate` coincide in the samples, but
> do not assume universal equality — each metric keeps the field proven for its purpose. The
> contribution table (approximate reference) uses `availableForTradingDate`; TWR (precise return
> math) uses `reportDate`.

### 2.3 Repository / service [SERVICE]
- New service function assembling the table: e.g. `get_contribution_wealth_series(conn)` →
  ordered rows with the five columns above. Reads: transaction ledger (contribution dates +
  cumulative amounts) and `daily_nav` (consolidated `total` per date). Compute-on-read; no new
  storage.
- Consolidated cumulative contribution = running sum of DEPOSIT/WITHDRAWAL amounts (CAD) across
  all accounts, ordered by ledger date.
- For each row's date (availableForTradingDate), resolve Total Value via a **simple nearest-NAV
  lookup** against the summed daily NAV series (NAV on that date, else nearest available). This is
  a reference figure, not a precise one — no settlement/roll-forward rule. Only if no NAV data
  exists at all near that date → None → "—".

### 2.4 Rendering [UI]
- Render the five columns; format CAD dollars and % consistently with existing tables. Negative
  Change/Growth shown plainly. None → "—".

---

## 3. Minimal return metrics on the main screen

### 3.1 Return definitions (LOCKED — contribution-aware simple return; NOT (end−begin)/begin) [SERVICE]
- **Overall Simple Return** = `(total value incl. cash − total contribution) / total contribution`
  (consolidated). Guard divide-by-zero → None.
- **Per-account Simple Return** = same formula at account scope:
  `(account value − account contribution) / account contribution`.
- **TWR:** per-account = direct consume from `ChangeInNAV.twr`; consolidated = computed from the
  merged daily NAV + reportDate-aligned flows (already built + validated in 2.31).
- **Per-stock %gain/loss** = per-position USD return `(value − cost basis)/cost basis` (already
  in 2.3).

> Do NOT use `(end value − beginning value)/beginning value` for any headline return — it ignores
> contributions and blows up on funded accounts (the 588% class of error). The contribution-aware
> formula above is the correct "simple return." `(end−begin)/begin` is only valid as a
> cash-flow-free *period* return (a possible future Performance-screen metric, out of scope here).

### 3.2 Cards / display [UI]
- **Top cards:** consolidated TWR and consolidated Simple Return (labeled distinctly — "TWR" and
  "Simple Return"; they differ by design, the gap is contribution-timing information).
- **Accounts section:** per-account TWR and Simple Return columns (extends the Accounts section
  already carrying nicknames / EOD timestamps).
- **Holdings/Drilldown:** per-stock %gain/loss (USD return) column.
- Keep the main screen minimal — deeper time-series analysis is Phase 3.1, not here.

---

## Date Semantics (document — two conventions, each correct for its purpose)

- **Contribution table:** uses `availableForTradingDate` (fallback chain) for the row date, and a
  simple nearest-NAV lookup for the reference Total Value. Because availableForTradingDate ≈ the
  NAV reportDate where cash lands, the row is internally self-consistent (no deliberate
  misalignment). The table is an approximate wealth-context view.
- **TWR (2.31):** uses `reportDate`-aligned flows for precise return math. Unchanged and
  validated.

These two are separate on purpose (approximate reference vs. precise return). They coincide in the
current samples but each keeps the field proven for its purpose — do NOT repoint TWR to
availableForTradingDate without re-running the per-account TWR validation. State this in code
comments.

---

## Granularity note for Phase 3.3 (Balance Trend)

The contribution table is **sparse** (rows only at contribution events). The Balance Trend chart
(3.3) needs the **dense daily NAV series** (`daily_nav`), not these sparse rows. Do not build 3.3
by charting the table rows — it would give a milestone-spaced line, not a daily curve. Table and
chart draw from related but different granularities: table = contribution events; chart = daily
NAV.

---

## Tests
- **Contribution filter & date:** one row per `availableForTradingDate`, not per daily NAV date;
  same-day multi-account contributions → one row, contribution = sum. Fallback chain used when
  availableForTradingDate is empty.
- **Total Value (reference):** for a contribution with availableForTradingDate=Feb-2, Total Value
  = consolidated NAV at/nearest Feb-2 (simple lookup, not a settlement-precision rule). Labeled
  reference.
- **TWR unchanged:** TWR flows still use reportDate; per-account TWR still reproduces IBKR
  (14.11 / 28.70). Guard that the table's date change did NOT alter TWR.
- **Growth:** = (Total Value − cumulative contribution)/cumulative contribution; matches the
  per-row simple return; None-safe on zero contribution.
- **Simple return (cards):** overall and per-account use the contribution-aware formula;
  regression guard that `(end−begin)/begin` is NOT used (a funded account must not show a wild %).
- **Consolidated:** cumulative contribution and Total Value are summed across accounts.
- Existing suite green (this is additive + display; the underlying NAV/TWR/return data already
  exists).

## Verification
- Main screen shows facts + minimal cards (TWR, Simple Return, per-account, per-stock).
- Contribution table: one row per contribution date, consolidated, with Total Value (NAV-aligned),
  Change In Value, Growth. No per-daily-NAV noise rows.
- Navigation scaffold to the (stub) Portfolio Analysis screens present.

## Out of scope (Phase 3)
- The Portfolio Analysis screens themselves (Performance / Allocation / Balance Trend / What-If)
  beyond navigation stubs.
- TWR/return *time series* charts (3.1), allocation pies (3.2), balance-trend dense chart (3.3).
- IRR (deferred — no reliable data source), and `(end−begin)/begin` period returns.
