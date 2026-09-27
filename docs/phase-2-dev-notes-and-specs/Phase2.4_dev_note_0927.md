# Phase 2.4 Dev Note 0927 - Contributions and Return Metrics

Date: 2026-09-27

This note documents the Phase 2.4 housekeeping work implemented from
`phase2.4-housekeeping-spec_v2.md`.

Reference artifacts:

- `phase2.4-housekeeping-spec_v2.md`
- `phase2.4-housekeeping-spec.md` (superseded draft)
- `phase2.31-twr-flow-alignment-patch.md`

## Scope and Decisions

This implementation covered:

- Section 2: the enriched, sparse contribution wealth-reference table.
- Section 3: minimal return metrics on the main dashboard.

The existing TWR calculation was intentionally left unchanged. In particular:

- Per-account TWR still comes directly from the latest IBKR `ChangeInNAV.twr`.
- Consolidated TWR still uses the validated Phase 2.31 merged daily NAV calculation and
  `reportDate`-aligned external flows.
- `app/services/nav.py` was not modified.
- Contribution-table dates do not feed into TWR.

The navigation scaffold from Section 1 and the deeper Phase 3 analysis screens remain outside this
implementation.

## 1. Contribution Date Enrichment

### Transaction model

Added `settle_date` to `ParsedTransaction` and the SQLite `transactions` table.

IBKR cash transaction parsing now retains:

- `availableForTradingDate` as `available_date`
- `reportDate` as `report_date`
- `settleDate` as `settle_date`
- the existing ledger date as `txn_date`

The schema version was raised to v12 with a preserving migration. Existing transaction rows are
retained and receive a nullable `settle_date` column.

Duplicate transaction ingestion remains idempotent. When the same external transaction is seen
again, missing alignment dates can be backfilled without inserting another ledger row.

### Contribution-table date semantics

Contribution events use this effective-date precedence:

```text
available_date -> report_date -> settle_date -> txn_date
```

This date represents when contributed cash became usable for the wealth-reference view. It is
separate from the `report_date` convention used by TWR.

## 2. Contribution Wealth Reference

Added a compute-on-read contribution series that produces one consolidated row per contribution
date rather than one row per daily NAV observation.

Each row contains:

- Date available
- Cumulative contributions in CAD
- Total portfolio value in CAD, marked as a reference value
- Change in value
- Growth percentage

Same-day contributions across accounts are grouped into one row. Deposits and withdrawals remain
signed, so cumulative contributions represent net external capital.

Reference portfolio value is selected from consolidated daily NAV using a bounded nearest-date
lookup. This is deliberately approximate context, not a precision performance calculation. If no
suitable NAV exists, the value, change, and growth fields remain unavailable.

Growth uses:

```text
growth = (reference total value - cumulative contributions)
         / cumulative contributions
```

Zero cumulative contribution is guarded and produces no percentage.

### Reusable UI component

Moved the contribution table into:

`templates/components/contribution_table.html`

This keeps it self-contained so it can later move from the main dashboard to the Phase 3 Balance
Trend screen without duplicating its rendering logic.

## 3. Contribution-Aware Simple Return

Added account-scoped contribution aggregation and exposed stable account IDs through the portfolio
read model. This avoids grouping accounts by display label.

The dashboard now calculates:

```text
overall simple return =
    (consolidated positions + consolidated cash - consolidated contributions)
    / consolidated contributions

account simple return =
    (account positions + account cash - account contributions)
    / account contributions
```

These are since-inception, contribution-aware comparisons. The implementation does not use
`(ending value - beginning value) / beginning value`, which would misstate funded accounts.

### Completeness safeguards

Simple Return is withheld and rendered as `-` when its input is incomplete:

- cumulative contribution is zero;
- a required USD-to-CAD conversion is unavailable;
- an account cash balance cannot be converted;
- one or more position marks are missing.

This prevents a valid-looking percentage from being calculated from only the known subset of an
account or consolidated portfolio.

## 4. Portfolio and Cash Service Changes

The cash service now returns account-scoped:

- stable account ID;
- CAD and USD cash;
- net cash in CAD;
- net contributions in CAD;
- missing-FX status.

`AccountSummary` now includes:

- positions value in CAD;
- cash value in CAD;
- total value in CAD;
- cumulative contributions in CAD;
- per-account TWR;
- per-account Simple Return.

Accounts represented only by cash, contributions, or NAV data can still appear in the summary even
when they have no current position rows.

## 5. Dashboard Changes

Added top-level cards for:

- TWR
- Simple Return

Expanded the Accounts table with:

- Positions CAD
- Cash CAD
- Total value CAD
- Contributions CAD
- TWR
- Simple Return

Added `% Gain/Loss` to:

- Consolidated Holdings
- Account Drilldown

Per-stock gain/loss remains the existing USD return:

```text
(position value - cost basis) / cost basis
```

Positive returns use the existing green profit style and negative returns use the red loss style.

## 6. Tests and Verification

Added and updated tests covering:

- Flex `settleDate` parsing and transaction persistence;
- preserving schema migration to v12;
- duplicate-ingest alignment-date enrichment;
- effective contribution-date fallback order;
- same-day multi-account contribution grouping;
- cumulative signed contributions;
- nearest NAV reference values;
- zero-safe reference growth;
- account-scoped Simple Return;
- cash inclusion in account and consolidated value;
- zero-contribution behavior;
- missing contribution/cash FX behavior;
- incomplete position-mark behavior;
- unchanged Phase 2.31 TWR outputs.

Verification completed on 2026-09-27:

- Full test suite: `62 passed`
- Python compilation: passed with bytecode redirected to `/tmp`
- Jinja dashboard render: passed
- `git diff --check`: passed
- `app/services/nav.py`: no changes

## Known Boundaries

- Simple Return is not annualized and does not account for contribution timing. TWR remains the
  timing-aware performance metric.
- Per-stock gain/loss is shown in USD/native position terms as specified, not as a new CAD return
  calculation.
- Contribution-table NAV values are approximate reference values and must not be used as a
  substitute for the dense daily NAV series required by the future Balance Trend chart.
- Phase 3 performance charts, allocation views, Balance Trend, and What-If analysis remain
  deferred.
