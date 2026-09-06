# Dev Spec — T+1 Lag Handling, PnL-Ready Gating, and Timestamp Display

## Context (from progressive-load testing)

Two real loads revealed IBKR's actual behavior:
- IBKR is effectively a **T+1 source** for complete data. A statement for date T pulled during/
  just after overnight generation comes back with **all PnL fields zeroed** and an explicit
  Message: *"Realized P/L is not ready and has been disabled for this statement."* The
  complete, PnL-ready statement for T isn't reliably available until ~late T+1.
- Evidence: Sept-1 statement generated 01:00 Sept-2 → PnL disabled (zeros). Sept-2 statement
  generated 23:56 Sept-3 → PnL populated correctly.

Also confirmed **not bugs** (no action): the USD-PnL-positive / CAD-PnL-negative divergence
(META) is the dual-PnL decomposition working — FX loss outweighing stock gain
(`unrealizedCapitalGainsPnl + unrealizedlFxPnl = fifoPnlUnrealized`, reconciled to the cent);
and the displayed avg cost is the derived USD cost (correct), not the raw CAD `cost_basis_price`
column.

## Decisions (locked)

1. **Accept the T+1 lag.** Don't fight IBKR's schedule with same-day retries. The dashboard
   shows the latest *complete* EOD; on days where the statement is incomplete/pending, it keeps
   showing the last complete EOD (the "as of" date simply doesn't advance).
2. **Gate on PnL-readiness.** A statement flagged PnL-not-ready must NOT be finalized as an
   authoritative snapshot — this prevents the load-1 "silently stored zeros" outcome.
3. **Expose timestamps** so a reader always knows which day's data they're seeing (a stale mark
   price without a date is dangerous):
   - Top-level prominent **"as of" close date**.
   - **All three timestamps per account** in the Accounts section.
4. **Mixed-date accounts:** show each account's own dates (don't hide fresh data), but **warn
   prominently** when accounts differ.

---

## Part 1 — PnL-ready handling (ingest fully; NULL, don't skip)

### Principle (revised)
"PnL not ready" does NOT mean the statement is worthless. An incomplete statement still has
correct positions, `markPrice`, `positionValue`, trade data (so **derived USD PnL is fully
correct**), cash report, lots, and contributions. Only IBKR's own `fifoPnlUnrealized` (CAD PnL)
and its capital-gains/FX split are missing. So we **ingest the statement fully** and represent
only the missing CAD PnL as **NULL** — never as 0.

The original load-1 bug was fundamentally a **null-vs-zero** problem: storing
`fifo_pnl_unrealized = 0` reads as "this position broke even" (false); the honest
representation is NULL = "not reported by IBKR for this statement."

### Detection
`app/ingestion/ibkr_flex.py`: detect PnL-not-ready via:
- **Primary (authoritative):** a `Message` node containing "Realized P/L is not ready"
  (case-insensitive substring). This is IBKR's explicit signal.
- **Backup (flag only, do NOT auto-null):** all OpenPositions have `fifoPnlUnrealized == 0`/null
  while `positionValue` is populated. Because a portfolio *could* (improbably) be genuinely flat,
  this fingerprint alone should **raise a flag/warning for attention**, NOT trigger auto-NULL —
  only the Message node confidently drives NULLing. This prevents destroying a legitimately-flat
  portfolio's real zeros.
- Expose `pnl_ready` (bool) + optional message text on the parsed statement metadata.

### Ingest behavior
`app/routes/dashboard.py` (`_ingest_flex_xml`):
- **Always** store evidence (raw XML) — unchanged.
- **Always** ingest and upsert the full snapshot — positions, values, USD-derived PnL, cash,
  lots, contributions all populate normally regardless of `pnl_ready`.
- If `pnl_ready` is **False** (Message present): write the CAD-PnL fields
  (`fifo_pnl_unrealized`, `unrealized_capital_gains_pnl`, `unrealized_fx_pnl`) as **NULL**, not 0.
  Record run status "CAD PnL pending (IBKR P/L not ready) for <toDate>".
- If `pnl_ready` is **True**: populate CAD PnL fields normally.
- When a later run pulls the now-complete statement for the same `toDate` (different
  content_hash/whenGenerated), the upsert fills the CAD PnL from NULL to the real value.

### Display
- CAD PnL renders as **"pending"** (or "—") when NULL, never as $0.00. All other columns
  (value, USD PnL, cash) display their valid values. So the dashboard is fully useful during the
  pending window; only the one field is marked unavailable.

## Part 2 — Timestamp display

### Data plumbing
Ensure the service layer surfaces, per account, the three timestamps already stored on the
snapshot rows: `snapshot_date` (statement `toDate`), `statement_generated_at` (`whenGenerated`),
`ingested_at`. Add them to `AccountSummary` (and whatever feeds the Accounts section) if not
already exposed. Add a consolidated/top-level "as of" to `DashboardData`.

### Top-level "as of"
- Compute the set of distinct `snapshot_date`s across accounts.
- **All agree** → prominent header: e.g. **"Data as of Sept 2, 2026 (market close)"**.
- **Disagree** → show the **latest** date with a warning marker: **"Data as of Sept 2, 2026
  — ⚠ accounts have mixed dates (see Accounts)"**.
- This header is the anti-stale-price safeguard — it must be visible without scrolling.

### Accounts section (per account)
Add three columns / a detail block per account row: **As of (close date)**,
**Statement generated**, **Ingested**. This extends the EOD column already planned for the
Accounts section. Format dates readably (e.g. "Sept 2, 2026", "Sept 3 23:56", "Sept 4 03:56").

### Mixed-date warning
- Trigger whenever any two accounts' `snapshot_date` differ.
- Prominent banner (top and/or Accounts header) with actionable wording: e.g. **"Accounts are
  showing different close dates — RRSP is one day behind (statement pending at IBKR)."**

### Consolidated sections during a mismatch
- The grand total, Return on Contributions card, and Consolidated Holdings blend across dates
  when accounts differ (unavoidable if showing each account's latest-complete data).
- Carry the same **⚠ mixed-date** marker on the consolidated total / cards during a mismatch,
  so a blended figure isn't read as a clean single-date snapshot.

---

## Tests
- **PnL-ready detection:** load-1 evidence XML (Message present) → `pnl_ready == False`; normal
  statement → `True`. All-zero-without-Message → flag raised, NOT auto-null.
- **Null-not-skip:** ingesting a not-ready statement fully populates positions/values/USD-PnL/
  cash/lots, with CAD-PnL fields written as **NULL** (not 0). Run status notes "CAD PnL pending".
- **Backfill:** a later complete statement for the same `toDate` upserts CAD PnL from NULL to the
  real value.
- **Display:** NULL CAD PnL renders as "pending"/"—", never $0.00; other columns show valid data.
- **Timestamp exposure:** AccountSummary carries all three timestamps; top-level "as of"
  reflects the latest; agree → no warning, disagree → warning flag set.
- **Mixed-date warning:** two accounts with different snapshot_date → warning true; consolidated
  sections flagged.

## Verification
- Reading the dashboard, the close date is unmistakable up top; each account shows its own
  three timestamps; a lagging account triggers a visible warning and the consolidated figures
  are marked as blended.
- A PnL-not-ready pull (like load 1) no longer stores zeros as truth — it's skipped with a
  clear status, and the dashboard keeps the last good EOD.

## Notes
- This makes the accepted T+1 lag *safe and legible*: the visible "as of" date is what makes
  "show last complete EOD" trustworthy to read.
- Scheduler cadence (separate deployment spec) pairs with this: run daily; incomplete statements
  are naturally superseded on a later run. Evening/T+1-aware timing still applies, but gating
  makes the system robust even if a run catches an incomplete statement.

---

## Part 3 — "Refresh all" as the canonical operation (workflow decision)

**Decision:** "Refresh all" is the canonical user action. The per-account refresh buttons are
**debugging-only**. This aligns the manual workflow with the scheduler, which naturally performs
a refresh-all (it won't click individual accounts) — so manual and automated behavior share one
code path and behave identically.

### What refresh-all does and does NOT guarantee
- **DOES** remove the *operator* cause of mismatched dates (clicking login1 today, login2
  tomorrow) — both accounts are fetched in the same run.
- **Does NOT** guarantee both accounts land on the same `snapshot_date`. `snapshot_date` comes
  from each statement's own `toDate` (set by IBKR), not from when we fetch. If IBKR has
  completed login1's statement for date T but login2's T statement is still PnL-not-ready, a
  single refresh-all yields login1@T-complete and login2@(T-1) — same fetch, different snapshot
  dates. IBKR's per-account generation timing is not perfectly synchronized.

### Consequences (do NOT drop these)
- **The mixed-date warning (Part 2) remains required.** Refresh-all reduces mismatch frequency
  but cannot eliminate the IBKR-readiness cause. With refresh-all as the discipline, a mixed-date
  state now *means* "IBKR had one account incomplete" — a useful diagnostic signal, not operator
  error.
- **Per-account gating is independent within refresh-all.** Refresh-all must apply the PnL-ready
  gating (Part 1) to each account separately: finalize the accounts whose statements are ready,
  skip/gate the ones that aren't (leaving their last complete EOD), and raise the mixed-date
  warning when the results land on different dates. It is **partial-success, not all-or-nothing**
  — one account being not-ready must not fail the whole operation or force the ready account back
  to an older date.
- **Refresh-all == the scheduler's operation.** Implement them as one path so scheduled and
  manual refreshes are behaviorally identical.

---

## Part 4 — Run schedule (daily freshness + Saturday completion)

Because incomplete statements are now ingested (not skipped) with CAD PnL marked pending,
catching an incomplete statement is low-cost — so the daily run can favor **freshness**.

### Daily run — early AM, business days
- Fire **early morning EST** on business days (Tue–Sat, capturing the prior trading day's
  close). This gets fresh value + USD PnL + cash as soon as IBKR has *any* statement for the
  prior close; CAD PnL may be pending (NULL → "pending") until the complete statement exists.
- Accepts the T+1 lag (dashboard "as of" = most recent close). No skipping — pending CAD PnL is
  filled by a later run.
- Uses **refresh-all** (== the manual canonical operation, Part 3), per-account gating applied
  independently.

### Saturday completion / reflection run
- Fire **Saturday morning EST**. By the weekend IBKR has fully generated and settled the week's
  statements, so `fifoPnlUnrealized` is complete for the week's closes.
- Purpose: a **completion pass** — upsert the now-complete CAD PnL onto the week's partitions
  that were pending (NULL → real value), producing a complete weekly snapshot.
- Doubles as the trigger for the **weekend reflection workflow**: review the week's (now
  complete) performance, reflect on positions, plan next week's trading/focus. Front-loading it
  to Saturday puts reflection at the *start* of research time, not Sunday night.

### Caveat to verify empirically (via evidence store / whenGenerated)
- Confirm whether a single Saturday Flex pull (YTD range) completes CAD PnL for **all** of the
  week's mid-week partitions, or only the **latest** close's PnL (if IBKR reports point-in-time
  PnL only). If the latter, mid-week partitions may remain pending; the weekly snapshot's CAD
  PnL would be complete for Fridays/weekends but potentially stale mid-week. This affects whether
  the growth chart's CAD-PnL line is daily-complete or weekly-complete. Decide handling once the
  first weeks of `whenGenerated`/evidence data show IBKR's actual behavior.

### Run time tuning
- Start times are hypotheses. The `statement_generated_at` (`whenGenerated`) column now records
  IBKR's actual generation moment on every load. After 1–2 weeks, use the observed distribution
  to tune the daily run time (earlier if data is reliably ready sooner). Gating/null handling
  makes a suboptimal time low-risk.

### Future direction (note, not build)
- The Saturday complete snapshot points toward a distinct **weekly-summary / reflection view**
  (separate from the daily dashboard): daily = "where do I stand now"; weekly = "how did the week
  go, what did I learn, what's next." Connects to the eventual trade-journal/reflection workflow.
