# Dev Spec — Phase 2.31 Patch: Fix TWR Cash-Flow Date Alignment

## Bug

The consolidated TWR computation does not reproduce IBKR's authoritative per-account TWR:
computed RRSP TWR = **588%** vs IBKR's `ChangeInNAV.twr` = **14.69%**. (Per-account TWR shown on
the dashboard is fine — it's consumed directly from IBKR; only the *computed* consolidated TWR is
wrong, and it uses the same flawed inputs.)

### Root cause — cash-flow date misalignment
`compute_twr` nets each day's external cash flow against the NAV change. The flow dates currently
come from the **transaction ledger** (`transactions.txn_date`, derived from IBKR's `dateTime` /
settle date), but the **daily NAV series recognizes the cash on a later date** (settlement/clearing
lag). So a deposit recorded Jan-29 doesn't appear in NAV until Feb-4 → the algorithm sees a huge
unexplained NAV jump on Feb-4 with `flow=0` and books it as a ~500% "investment return."

Verified: on RRSP, Feb-04 shows `prev=4996.85 → nav=29966.35, flow=0.00 → period_return=+499.7%`,
which alone drives the 588% result. Aligning the flows to their NAV-recognition dates gives
~14.1% ≈ IBKR's 14.69%.

### The fix (Option 1 — mirror IBKR)
IBKR's `CashTransaction` node carries three dates:
- `dateTime` / `settleDate` — when the transaction occurred/settled (~late January).
- **`reportDate`** — when it is recognized in the account statement / NAV (early February).

Verified on the real data:
| amount | dateTime | settleDate | **reportDate** |
|---|---|---|---|
| 5000 | 20260127 | 20260127 | **20260202** |
| 25000 | 20260129 | 20260129 | **20260204** |

The `reportDate` (Feb-2 / Feb-4) is exactly when the NAV series recognizes the cash — matching the
dates that reproduce IBKR's TWR. **So TWR cash flows must be dated by `CashTransaction.reportDate`,
not `txn_date`.**

---

## Scope

Small, contained patch. Does NOT change the ledger, transaction dedup, or any non-TWR figure.
Only changes **which date the TWR cash-flow series uses**. The transaction ledger keeps using its
existing dates (correct for contributions display / cash balances); only the TWR flow-alignment
switches to `reportDate`.

---

## Changes

### 1. Ingestion — capture `reportDate` on cash transactions
`app/ingestion/ibkr_flex.py`: in the `CashTransaction` parse, capture **`reportDate`** (normalize
`20260202` → `2026-02-02`) alongside the existing fields. The current parser derives `txn_date`
from `dateTime`/settle — keep that unchanged for the ledger, but additionally expose the
NAV-recognition `report_date` on the parsed cash-flow model (`ParsedTransaction` or a dedicated
cash-flow accessor).

- If `reportDate` is absent on a row, fall back to the existing `txn_date` (so nothing breaks for
  sources without it, e.g. CIBC — out of scope but shouldn't crash).

### 2. Storage — make the NAV-recognition date available to the TWR path
Two options; pick the lighter one that fits the codebase:
- **(a)** Add a `report_date` column to `transactions` (nullable), populated from
  `CashTransaction.reportDate` on ingest. TWR flow queries read `report_date` (fallback
  `txn_date`).
- **(b)** If altering `transactions` is undesirable, store deposit/withdrawal recognition dates in
  a small dedicated structure the TWR path reads.
- Recommend **(a)** — it's one nullable column, keeps the NAV-recognition date attached to the
  transaction it belongs to, and TWR just selects the right date. Requires a `SCHEMA_VERSION` bump.

### 3. Service — TWR flows use `reportDate`
`app/services/nav.py` → `contribution_cashflows_cad` (the function feeding `compute_twr` and
`get_consolidated_twr`): change the date it emits from `txn_date` to **`report_date`** (fallback
`txn_date` when null). Group DEPOSIT/WITHDRAWAL by `report_date`, currency → CAD, as today.

- **Important:** this change is scoped to the **TWR cash-flow series only.** The contributions
  *display* series (the cumulative contributions table / Simple Return) should keep using the
  ledger `txn_date` — that's when *you* contributed, which is the right semantic for "contributions
  over time." Only TWR needs NAV-recognition alignment. Do not repoint the contributions display to
  `reportDate`.

### 4. Validation gate (make it a permanent test)
Add a test that **fails the build if computed per-account TWR doesn't reproduce IBKR's
`ChangeInNAV.twr`** — this is the check that would have caught the bug pre-merge:
- For each account fixture (RRSP and Corporate), run `compute_twr` on that account's daily NAV +
  its `reportDate`-aligned flows, and assert it matches `ChangeInNAV.twr` within a tolerance
  (**≤ ~0.75 percentage points** — the split-guess landed 14.11 vs 14.69, a ~0.6pp gap from
  same-day-granularity effects; tighten if the real fix does better, but don't demand exact —
  daily-granularity TWR won't match IBKR's intraday method to the basis point).
- Assert the **consolidated** TWR is computed (not None) and is NOT equal to a naive NAV-weighted
  average of the per-account TWRs (guards against regressing to the wrong method).

> Tolerance note: IBKR computes TWR with intraday precision; our daily-NAV linked method is a
> close approximation, not an exact reproduction. The gate confirms we're in the right *ballpark*
> (a few tenths of a percent), which proves the flow alignment is correct — not that we match IBKR
> to the cent. A pre-fix result of 588% vs a post-fix result of ~14% is the signal that matters.

---

## Tests
- **reportDate parse:** `CashTransaction` with `reportDate=20260202` → parsed cash flow carries
  `report_date=2026-02-02`; `dateTime=20260127` still yields ledger `txn_date=2026-01-27`.
- **TWR alignment (the fix):** RRSP computed TWR reproduces `ChangeInNAV.twr=14.69` within
  tolerance using `reportDate` flows; using `txn_date` flows it does NOT (regression guard — the
  bug reappears if someone repoints it back).
- **Corporate:** computed TWR reproduces `ChangeInNAV.twr=29.01` within tolerance.
- **Consolidated:** computed from summed daily NAV + reportDate flows; not None; not the naive
  weighted average.
- **Contributions display unchanged:** the cumulative contributions series / Simple Return still
  use `txn_date` (assert the display series is unaffected by this patch).
- Existing suite green.

## Verification
- Ingest both accounts. Per-account computed TWR ≈ IBKR (14.69 / 29.01). Consolidated TWR is a
  sensible value between/around them (not 588%, not a naive average). Contributions table and
  Simple Return unchanged.

## Why this is the right fix (Option 1)
Consistent with the project's "mirror IBKR" principle: use IBKR's own `reportDate` — the date it
recognizes the cash in the NAV — rather than inferring settlement lag or detecting flows from NAV
jumps. The flow dates then align with the NAV series by construction, and TWR reproduces IBKR's
figure. The permanent validation test ensures this can't silently regress.

## Out of scope
- Any change to the ledger, dedup, contributions display, or non-TWR figures.
- Matching IBKR's TWR to the basis point (daily-granularity method is a close approximation).
- CIBC (no reportDate; falls back to txn_date; out of scope anyway).
