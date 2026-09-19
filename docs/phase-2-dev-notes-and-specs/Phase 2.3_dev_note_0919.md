# Phase 2.3 Dev Note 0919 - NAV, Returns, and TWR Flow Alignment

Date: 2026-09-19

This note documents the Phase 2.3 NAV and return implementation and the Phase 2.31 cash-flow alignment patch.

Reference artifacts:

- `phase2.3-nav-twr-spec_v2.md`
- `phase2.31-twr-flow-alignment-patch.md`
- `new_flex_sample_v2.xml`
- `rrsp_v1_phase2.31.xml`
- `testing_v3_phase2.31.xml`

## Goal

Add broker-authoritative NAV history and account TWR, then calculate a best-effort consolidated TWR across multiple IBKR accounts.

The implementation keeps these return views separate:

- Per-account TWR comes directly from IBKR `ChangeInNAV.twr`.
- Consolidated TWR is calculated locally from consolidated daily NAV and external cash flows.
- Simple Return remains a comparison between current value and cumulative contributions.

## Phase 2.3 Changes

### Flex NAV parsing

Updated `app/ingestion/ibkr_flex.py` to parse:

- `ChangeInNAV` into statement-level NAV and return summaries.
- `EquitySummaryByReportDateInBase` into daily account NAV observations.
- The authoritative daily `total` field directly instead of reconstructing NAV from components.

The component fields `cash`, `stock`, `dividendAccruals`, and `interestAccruals` are retained for inspection.

### NAV storage

Added two SQLite tables:

- `nav_summary`: one account summary per statement end date, including IBKR TWR and NAV change components.
- `daily_nav`: one authoritative base-currency NAV value per account and report date.

NAV writes are idempotent by account/date and retain evidence provenance through `content_hash` and `ingested_at`.

### Portfolio and return services

Added repository and service support for:

- Latest account NAV summaries.
- Account and consolidated daily NAV series.
- Locally linked consolidated TWR.
- Historical portfolio value alongside cumulative contributions.
- Holding return percentage.
- Holding weight by market value and cost.
- CAD-normalized cost basis.
- Dashboard Simple Return and consolidated TWR.

### Initial TWR calculation

The initial implementation summed daily NAV across accounts and removed external flows before linking daily returns:

```text
daily return = (ending NAV - external cash flow) / previous NAV - 1
consolidated TWR = product(1 + daily return) - 1
```

The first version sourced flow dates from `transactions.txn_date`.

## Phase 2.31 Bug

RRSP testing exposed a severe mismatch:

- Computed using ledger dates: approximately `588.52%`
- IBKR authoritative TWR: approximately `14.69%`

The ledger recorded contributions when initiated or settled, but IBKR daily NAV recognized them later.

| Amount | Ledger date | Report date | Available date |
|---:|---|---|---|
| CAD 5,000 | 2026-01-27 | 2026-02-02 | 2026-02-02 |
| CAD 25,000 | 2026-01-29 | 2026-02-04 | 2026-02-04 |

Removing a flow before it appeared in NAV caused the later NAV increase to be misclassified as investment performance.

## Phase 2.31 Patch

### Separate alignment dates

Extended `ParsedTransaction` and `transactions` with:

- `report_date`: IBKR statement/NAV recognition date.
- `available_date`: IBKR `availableForTradingDate`.

The original `txn_date` remains the ledger and user-facing contribution date.

### TWR date precedence

TWR cash flows now use:

```text
available_date -> report_date -> txn_date
```

The two real fixtures drove this decision:

- RRSP availability and report dates both matched NAV recognition.
- Corporate contains deposit advances, cancellations, and delayed availability.
- `availableForTradingDate` produced a closer Corporate result than `reportDate` alone.

This precedence applies only to TWR. Contribution history and Simple Return still use `txn_date`.

### Schema migration

Bumped SQLite schema version from 10 to 11.

The preserving migration adds nullable `report_date` and `available_date` columns with `ALTER TABLE`. Existing transactions, NAV history, and evidence are not dropped. Migration chains from versions 8 and 9 now continue through version 11.

### Duplicate enrichment

Deduplication remains based on `(source, external_id)`.

When an existing transaction is encountered again, ingestion fills missing alignment dates. A successful Flex refresh or manual upload can therefore enrich the current database without a reset.

## Validation

| Account | IBKR TWR | Computed TWR | Difference |
|---|---:|---:|---:|
| RRSP | 14.6931% | 14.1092% | 0.5840 pp |
| Corporate | 30.6774% | 30.3678% | 0.3096 pp |

Both are within the agreed `0.75` percentage-point tolerance.

Using the old ledger dates produces invalid results:

- RRSP: approximately `588.52%`
- Corporate: approximately `-115.83%`

The locally linked consolidated TWR for the two fixtures is approximately `11.2528%`. It is linked from summed daily NAV and aligned flows, not calculated as a weighted average of account TWRs.

## Tests

Coverage includes:

- Parsing ledger, report, and available dates independently.
- Preserving version-10 transactions during migration to version 11.
- Enriching deduplicated transactions with missing dates.
- TWR date precedence and fallback behavior.
- Matching both real account fixtures within `0.75pp`.
- Proving the old ledger-date calculation remains materially wrong.
- Keeping the contribution display on ledger dates.
- Confirming consolidated TWR is not a naive weighted average.

Verification:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m pytest -p no:cacheprovider
```

Result:

```text
57 passed
```

## Decisions and Limitations

- IBKR account-level TWR remains authoritative.
- Consolidated TWR is a daily-granularity approximation because IBKR does not provide one authoritative return across separate logins.
- The local calculation cannot reproduce IBKR's intraday methodology exactly.
- Historical non-CAD flows still use the latest available USD/CAD rate. Current validation contributions are CAD, so this does not affect these results.
- Consolidated NAV currently assumes compatible account calendars. Missing account dates may require a future calendar-alignment policy.
- Existing rows receive alignment dates after the corresponding transactions are ingested again with the same source and external ID.

## Main Files Changed

- `app/ingestion/ibkr_flex.py`
- `app/models.py`
- `app/repository/db.py`
- `app/repository/nav.py`
- `app/repository/transactions.py`
- `app/routes/dashboard.py`
- `app/services/growth.py`
- `app/services/nav.py`
- `app/services/portfolio.py`
- `app/services/valuation.py`
- `tests/test_flex.py`
- `tests/test_repository_and_valuation.py`
