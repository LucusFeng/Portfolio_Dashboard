# Dev Note 0829 - Ingest Provenance

Date: 2026-08-29

This note documents the Phase 2 ingest provenance work implemented from
`ingest-provenance-spec.md`.

## Goal

Make the IBKR Flex ingest path safe for future scheduled EOD runs by separating three ideas:

- `snapshot_date`: the Flex statement EOD date, from `FlexStatement.toDate`.
- `statement_generated_at`: when IBKR generated the statement, from `FlexStatement.whenGenerated`.
- `ingested_at`: when this app wrote the data into SQLite.

The second goal was to keep the exact raw Flex XML that produced each dashboard number, so any
snapshot can be traced back to its source evidence.

## Why This Was Needed

Before this change, Flex snapshots used the local run date as the partition key. That made the
dashboard vulnerable to stale or mislabeled partitions:

- If the app ran on day 2 but IBKR still returned the day 1 statement, the app could write day
  1 facts under a day 2 partition.
- Repeated runs could create confusing history even when IBKR returned byte-identical XML.
- Debugging required manually comparing exported XML to database rows.

For scheduled EOD behavior, the data should be partitioned by the statement date, not the date
the job happened to run.

## Code Changes

### Flex statement metadata parsing

Updated `app/ingestion/ibkr_flex.py`:

- Added `parse_flex_statement_metadata(xml_text)`.
- Added `_datetime()` parser for IBKR timestamps like `20260802;172853`.
- Parses `FlexStatement.toDate` into `YYYY-MM-DD`.
- Parses `FlexStatement.whenGenerated` into ISO datetime format.
- Rejects XML containing multiple different `toDate` values, rather than silently assigning all
  rows to the wrong partition.

### Evidence store

Added `app/repository/evidence.py`:

- `store_evidence(...)`: hashes raw XML, gzip-compresses it, inserts it once, and returns the
  evidence ID, content hash, and whether it was new.
- `get_evidence(...)`: retrieves and decompresses raw XML by evidence ID or content hash.
- `list_evidence(...)`: lists metadata without returning the compressed blob.

The evidence hash is SHA-256 of the raw XML text.

### Schema update

Updated `app/repository/db.py`:

- Bumped `SCHEMA_VERSION` from `7` to `8`.
- Added `evidence_store` to the schema and table-drop list.
- Added provenance columns to snapshot/derived tables:
  - `statement_generated_at`
  - `ingested_at`
  - `content_hash`
- Added `content_hash` to `transactions` as well, so ledger rows can be traced to the exact XML
  that inserted them.

Affected tables:

- `transactions`
- `positions`
- `position_values`
- `cash_balances`
- `reconciliations`
- `evidence_store`

### Ingest flow

Updated `app/routes/dashboard.py`:

- `_ingest_flex_xml()` now parses statement metadata internally.
- Flex snapshot partitioning now uses `metadata.to_date`.
- `today_snapshot_date()` remains only as a fallback if Flex metadata is absent.
- API refresh and manual XML upload now both use the same evidence-backed ingest path.
- Run messages now include statement date and whether the XML evidence was `new` or `same`.

### Repository write paths

Updated repository functions to accept optional provenance arguments:

- `append_transactions(..., content_hash=...)`
- `upsert_position_values(..., statement_generated_at=..., ingested_at=..., content_hash=...)`
- `upsert_cash_balances(..., statement_generated_at=..., ingested_at=..., content_hash=...)`
- `rebuild_derived_state(..., statement_generated_at=..., ingested_at=..., content_hash=...)`
- `record_reconciliation(..., statement_generated_at=..., ingested_at=..., content_hash=...)`

Existing non-Flex tests and call sites can still omit these values.

### Reconciliation replacement

Changed reconciliation writes from unbounded append to replace-by-partition:

```sql
DELETE FROM reconciliations
WHERE snapshot_date = ? AND source = ?
```

This keeps reconciliation rows bounded for repeated same-statement ingests. The delete is scoped
by `source`, so login2 does not wipe login1's reconciliation rows when both logins share the
same statement date.

## Runtime Behavior

When a Flex XML file is ingested:

1. The app parses `toDate` and `whenGenerated` from the XML.
2. The app computes a SHA-256 `content_hash` for the raw XML.
3. The raw XML is gzip-compressed and stored in `evidence_store`, unless the same hash already
   exists.
4. Transactions, positions, position values, cash balances, and reconciliations are written with
   the same `content_hash`.
5. Snapshot tables are partitioned by statement EOD date.

Re-ingesting byte-identical XML:

- does not duplicate the raw XML blob;
- writes/upserts the same statement partition;
- records the evidence as `same` in the run message.

## Testing Added

Added and updated tests for:

- Flex `toDate` parsing.
- Flex `whenGenerated` parsing.
- Mixed statement-date guard.
- Evidence compression, dedup, and round-trip retrieval.
- Snapshot rows using statement date instead of run date.
- Transaction/snapshot/cash/reconciliation rows linked to `content_hash`.
- Reconciliation rows replacing within the same `snapshot_date + source` partition.

Verification command:

```bash
.venv/bin/python -m pytest -q
```

Result:

```text
44 passed
```

## Known Caveat

The app still uses dev-time schema resets. A future schema bump currently drops all app tables,
including `evidence_store`. Before the SQLite DB becomes the durable production archive, schema
changes should move from drop-and-rebuild to migrations, or the DB should be backed up before
schema changes.

## Decision

This is the first Phase 2 foundation item. It should remain in place before building an EOD
scheduler, because it makes repeated scheduled runs idempotent when IBKR returns the same Flex
statement more than once.
