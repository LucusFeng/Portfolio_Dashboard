# Dev Note 0906 - T+1 Lag, PnL Readiness, and Timestamp Display

Date: 2026-09-06

This note documents the Phase 2 changes implemented from
`tplus1-lag-and-timestamps-spec.md`.

IBKR Flex API testing is currently blocked because the Flex API is unavailable. Code-level unit
and integration-style tests are in place, but real API validation should be completed once IBKR
is back online.

## Background

Progressive manual loading showed that IBKR Flex behaves like a T+1 source for complete PnL
data. A statement can be available for a close date while IBKR still marks P/L as not ready.
In that state, Flex may emit valid positions, values, marks, cash, trades, and contributions,
but report CAD PnL fields as zero with a message like:

```text
Realized P/L is not ready and has been disabled for this statement.
```

The important behavior change is that these zeros should not be stored as real CAD PnL. They
mean "IBKR has not published this field yet," not "the position broke even."

## Implemented Changes

### 1. PnL readiness detection

Updated `app/ingestion/ibkr_flex.py`:

- Added explicit detection for IBKR's P/L-not-ready message.
- Extended `FlexStatementMetadata` with:
  - `pnl_ready`
  - `pnl_message`
  - `pnl_warning`
- Added backup detection for the all-zero CAD PnL fingerprint.

Decision: the all-zero fingerprint is warning-only. It does not auto-null PnL fields unless the
explicit IBKR message is present. This avoids erasing a legitimately flat portfolio.

### 2. Ingest fully, NULL CAD PnL only

Updated `app/routes/dashboard.py`:

- Flex evidence is still always stored.
- Transactions, positions, position values, cash balances, lots, and reconciliations are still
  ingested.
- If `pnl_ready=False`, only the IBKR CAD PnL fields are replaced with `NULL` before storage:
  - `fifo_pnl_unrealized`
  - `unrealized_capital_gains_pnl`
  - `unrealized_fx_pnl`
- USD PnL remains available because it is derived from Flex value minus trade cost basis.
- A later complete statement for the same `toDate` can upsert the real CAD PnL into the same
  statement-date partition.

Note: this follows the revised principle in the spec: pending statements are ingested, not
skipped. The app withholds only the fields IBKR explicitly says are not ready.

### 3. Evidence-level PnL provenance

Updated `app/repository/db.py` and `app/repository/evidence.py`:

- Bumped schema from v8 to v9.
- Added a preserving migration from v8 to v9 so existing evidence/data is not dropped.
- Added these columns to `evidence_store`:
  - `pnl_ready INTEGER NOT NULL DEFAULT 1`
  - `pnl_message TEXT`
  - `pnl_warning TEXT`
- Updated evidence writes and metadata listing to include PnL readiness fields.

This keeps PnL readiness as a property of the source XML evidence. Snapshot rows remain linked
through `content_hash`.

### 4. Timestamp exposure in dashboard data

Updated `app/repository/portfolio.py`, `app/services/portfolio.py`, and
`app/services/valuation.py`:

- Portfolio rows now expose:
  - `snapshot_date`
  - `statement_generated_at`
  - `ingested_at`
  - `cad_pnl_status`
  - `pnl_message`
- `AccountSummary` now includes per-account timestamps:
  - `snapshot_date`
  - `statement_generated_at`
  - `ingested_at`
  - `cad_pnl_pending`
- Dashboard-level data now includes:
  - `as_of_date`
  - `has_mixed_snapshot_dates`
  - `has_pending_cad_pnl`

### 5. Dashboard display changes

Updated `templates/dashboard.html`:

- Added top-level `Data as of <date> market close` display.
- Added mixed-date warning banner.
- Added CAD PnL pending warning banner.
- Added account-level columns:
  - As of close
  - Statement generated
  - Ingested
  - CAD PnL pending
- CAD PnL now renders as `pending` when the DB value is `NULL` due to IBKR P/L not ready.
- Consolidated totals/cards are labelled as mixed-date when account snapshot dates differ.

## Tests Added / Updated

The automated test suite was expanded from 44 to 49 tests.

Covered cases:

- Explicit PnL-not-ready message sets `pnl_ready=False`.
- Normal statement keeps `pnl_ready=True`.
- All-zero CAD PnL without the explicit IBKR message creates `pnl_warning`, but does not
  auto-null CAD PnL.
- Pending statement still ingests positions, position values, cash balances, lots, and
  transactions.
- Pending statement stores CAD PnL fields as `NULL`, not zero.
- Later complete statement for the same `toDate` backfills CAD PnL from `NULL` to the real
  value.
- Dashboard service data exposes account timestamps.
- Mixed account dates set `has_mixed_snapshot_dates=True`.
- Pending CAD PnL sets `has_pending_cad_pnl=True`.

Verification command:

```bash
.venv/bin/python -m pytest -q
```

Latest result:

```text
49 passed
```

## Test Cases For Next Week When IBKR Is Back Online

Use **Refresh all** as the canonical workflow. Per-login refresh should remain a debugging tool
only.

### 1. Fresh API run with both logins

Steps:

1. Start the app.
2. Click **Refresh all**.
3. Confirm both logins refresh successfully or report isolated failures.
4. Open SQLite and check `evidence_store`.

Expected:

- One evidence row per login XML, unless IBKR returns byte-identical XML already stored.
- `statement_to_date` reflects the Flex statement close date, not the app run date.
- `statement_generated_at` is populated from IBKR `whenGenerated`.
- Dashboard top header shows the latest `Data as of` close date.

Suggested query:

```sql
SELECT id, source, ingest_kind, statement_to_date, statement_generated_at,
       pnl_ready, pnl_message, pnl_warning, ingested_at, raw_size, byte_size
FROM evidence_store
ORDER BY id DESC;
```

### 2. Pending PnL statement behavior

Run this when IBKR returns the P/L-not-ready message.

Expected:

- `evidence_store.pnl_ready = 0`.
- `evidence_store.pnl_message` contains the IBKR not-ready message.
- `position_values.fifo_pnl_unrealized` is `NULL`, not `0`.
- Dashboard shows CAD PnL as `pending`.
- Market value, quantity, price, cash, and USD PnL still display.

Suggested query:

```sql
SELECT pv.snapshot_date, a.label AS account, i.symbol,
       pv.value_native, pv.value_base, pv.fifo_pnl_unrealized,
       ev.pnl_ready, ev.pnl_message
FROM position_values pv
JOIN accounts a ON a.id = pv.account_id
JOIN instruments i ON i.id = pv.instrument_id
LEFT JOIN evidence_store ev ON ev.content_hash = pv.content_hash
ORDER BY pv.snapshot_date DESC, a.label, i.symbol;
```

### 3. Backfill when IBKR publishes complete PnL

Steps:

1. First ingest a pending statement for date T.
2. Later, after IBKR publishes complete PnL for the same date T, run **Refresh all** again.
3. Compare `position_values` for the same `snapshot_date`.

Expected:

- New evidence row if the XML content changed.
- Same `snapshot_date` partition is updated.
- CAD PnL moves from `NULL` to a real value.
- Dashboard no longer shows `pending` for that account/date.

Suggested query:

```sql
SELECT snapshot_date, source, COUNT(*) AS rows,
       SUM(CASE WHEN fifo_pnl_unrealized IS NULL THEN 1 ELSE 0 END) AS pending_rows,
       ROUND(SUM(fifo_pnl_unrealized), 2) AS cad_pnl
FROM position_values
GROUP BY snapshot_date, source
ORDER BY snapshot_date DESC, source;
```

### 4. Repeated same-statement idempotency

Steps:

1. Run **Refresh all** once.
2. Run **Refresh all** again after cooldown, while IBKR still returns the same XML.

Expected:

- `evidence_store` does not duplicate byte-identical XML.
- Run message says evidence is `same`.
- `positions`, `position_values`, `cash_balances`, and `reconciliations` stay on the same
  statement date.
- Reconciliation rows do not accumulate duplicates for the same `snapshot_date + source`.

Suggested query:

```sql
SELECT content_hash, source, statement_to_date, COUNT(*) AS copies
FROM evidence_store
GROUP BY content_hash, source, statement_to_date
HAVING COUNT(*) > 1;
```

This should return no rows.

### 5. Mixed-date account display

Run this if one login advances to a newer statement date while another remains one date behind.

Expected:

- Dashboard top header shows the latest available close date.
- Mixed-date warning banner appears.
- Accounts section shows each account's own close date, statement-generated timestamp, and
  ingest timestamp.
- Consolidated totals are marked as mixed-date.

Suggested query:

```sql
SELECT a.label AS account, MAX(pv.snapshot_date) AS latest_snapshot_date,
       MAX(pv.statement_generated_at) AS latest_statement_generated_at,
       MAX(pv.ingested_at) AS latest_ingested_at
FROM position_values pv
JOIN accounts a ON a.id = pv.account_id
GROUP BY a.label
ORDER BY a.label;
```

### 6. Evidence-based audit check

Pick one evidence row and confirm linked rows trace back to it.

Expected:

- Transactions, position values, cash balances, and reconciliations produced by the XML carry
  the same `content_hash`.

Suggested query:

```sql
SELECT id, content_hash, source, statement_to_date
FROM evidence_store
ORDER BY id DESC;
```

Then use the selected hash:

```sql
SELECT COUNT(*) AS transactions FROM transactions WHERE content_hash = 'paste_hash_here';
SELECT COUNT(*) AS position_values FROM position_values WHERE content_hash = 'paste_hash_here';
SELECT COUNT(*) AS cash_balances FROM cash_balances WHERE content_hash = 'paste_hash_here';
SELECT COUNT(*) AS reconciliations FROM reconciliations WHERE content_hash = 'paste_hash_here';
```

### 7. Saturday completion pass

Run a Saturday morning refresh after IBKR has completed the week's statements.

Expected:

- Latest available statements should generally be PnL-ready.
- `pnl_ready = 1` for the new evidence rows.
- Pending CAD PnL rows should be reduced or eliminated for the latest close.
- Use observed `statement_generated_at` values to refine the future scheduled run time.

## Open Questions For Live Testing

- Does a Saturday YTD Flex pull backfill CAD PnL for all prior pending mid-week dates, or only
  the latest statement close?
- Are `whenGenerated` times consistent enough to choose a stable weekday scheduled run time?
- Do both IBKR logins advance their statement `toDate` at the same cadence, or does one account
  regularly lag the other?
- Does the all-zero fingerprint ever appear without the explicit P/L-not-ready message in real
  Flex XML?

## Current Limitation

The code path is tested locally, but the real IBKR API path still needs live validation once
Flex service availability returns. Manual XML upload can still be used as a fallback to validate
parser and storage behavior while the API is unavailable.
