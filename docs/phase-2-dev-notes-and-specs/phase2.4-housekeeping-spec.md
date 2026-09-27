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
  | **Total Value** | consolidated NAV **as of the latest available NAV on/after the contribution settles** | from `EquitySummaryByReportDateInBase.total`, summed across accounts; rolls forward past weekends/holidays |
  | **Change In Value** | Total Value − cumulative Contribution | dollar value (CAD) |
  | **Growth** | Change In Value / cumulative Contribution | % — the contribution-aware simple return |

- **Total Value date rule:** for a contribution row dated T (ledger), Total Value = the
  consolidated NAV on the **first NAV report date ≥ the contribution's settlement date**. This is
  why the row's *label date* (T) and its *Total Value date* (settlement/NAV-recognition) can
  differ — intentional (see Date Semantics below).

### 2.3 Repository / service [SERVICE]
- New service function assembling the table: e.g. `get_contribution_wealth_series(conn)` →
  ordered rows with the five columns above. Reads: transaction ledger (contribution dates +
  cumulative amounts) and `daily_nav` (consolidated `total` per date). Compute-on-read; no new
  storage.
- Consolidated cumulative contribution = running sum of DEPOSIT/WITHDRAWAL amounts (CAD) across
  all accounts, ordered by ledger date.
- For each contribution date, resolve Total Value via the "first NAV date ≥ settlement" rule
  against the summed daily NAV series. If no NAV row exists yet on/after settlement (e.g. a
  contribution settling in the future), Total Value / Change / Growth are None → display "—".

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

## Date Semantics (document prominently — three intentional conventions)

Three different contribution/date conventions coexist **on purpose**. Do NOT "reconcile" them —
each is correct for its context:

1. **Contribution table — row filter & label:** ledger `txn_date` (human-readable "when I
   contributed").
2. **Contribution table — Total Value:** NAV-recognition date ("first NAV ≥ settlement"), so the
   value reflects the money having landed.
3. **TWR (2.31):** `reportDate`-aligned flows, for correct return math against the NAV series.

A future reader who "fixes" the apparent inconsistency will reintroduce bugs. State this in code
comments on the relevant functions.

---

## Granularity note for Phase 3.3 (Balance Trend)

The contribution table is **sparse** (rows only at contribution events). The Balance Trend chart
(3.3) needs the **dense daily NAV series** (`daily_nav`), not these sparse rows. Do not build 3.3
by charting the table rows — it would give a milestone-spaced line, not a daily curve. Table and
chart draw from related but different granularities: table = contribution events; chart = daily
NAV.

---

## Tests
- **Contribution filter:** table has one row per contribution *date*, not per daily NAV date;
  same-day multi-account contributions → one row, contribution = sum.
- **Total Value alignment:** for a contribution dated Jan-29 settling Feb-4, Total Value = the
  consolidated NAV on the first NAV date ≥ Feb-4 (not the Jan-29 NAV). Rolls forward past
  non-trading settlement dates.
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
