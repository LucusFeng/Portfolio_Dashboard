# Dev Spec — Ingest Provenance: Statement-Date Partitioning + Evidence Store

Combines two closely-related changes to the ingest path. Both are about provenance and both
modify `_ingest_flex_xml`, so they're implemented together and reinforce each other:

1. **Partition snapshots by the statement's EOD date** (not the run date) — fixes the observed
   stale-data / zero-CAD-PnL anomalies.
2. **Evidence store** — retain every ingested raw Flex XML, atomically with the data it
   produced, as a durable, traceable source of truth.

---

## Part 1 — Statement-date partitioning

### Problem
`snapshot_date = dt.date.today()` (run date) is used as the partition key for `positions`,
`position_values`, `cash_balances`, `reconciliations`. This conflates *when we ran* with *what
date the data represents*, producing stale/mislabeled partitions across runs (e.g. day-2 run
showing day-1 prices; and stale zero-CAD-PnL rows persisting under drifted run-date
partitions). A DB reset "fixes" it only by collapsing all partitions into one.

### What IBKR provides (verified on `FlexStatement`)
- **`toDate`** (e.g. `20260731`) — statement period-end = the EOD the data represents →
  **partition key**.
- **`whenGenerated`** (e.g. `20260802;172853`) — when IBKR generated the statement →
  detects "same statement re-ingested" vs. "genuinely new EOD".
- Ingest time (`datetime.now()`) → when our job wrote it → run audit.

### Principle (LOCK)
> Facts accumulate; snapshots partition by **statement EOD date**; derivations rebuild.
> - `transactions` — append-only, deduped. Never partitioned.
> - `positions`, `position_values`, `cash_balances`, `reconciliations` — partitioned by
>   statement `toDate`; delete-and-replace / upsert for date T; full history retained.
> - `lots` — pure derivation, wipe + rebuild.

### Changes
- **`app/ingestion/ibkr_flex.py`**: parse from `FlexStatement`:
  - `to_date`: `20260731` → `2026-07-31` (reuse `_date`).
  - `when_generated`: `20260802;172853` → ISO `2026-08-02T17:28:53`.
  - Expose both. If multiple `FlexStatement` nodes, use each statement's own `toDate` for its
    rows (normally identical across accounts in one login).
- **`app/routes/dashboard.py`** (`_ingest_flex_xml` and callers): replace
  `snapshot_date = today_snapshot_date()` with the parsed statement `to_date`. Thread
  `when_generated` and `ingested_at = datetime.now()` into the upserts. Keep
  `today_snapshot_date()` only as a logged fallback if `toDate` is somehow absent.
- **`app/repository/db.py`** (bump `SCHEMA_VERSION`): add `statement_generated_at TEXT` and
  `ingested_at TEXT` to `positions`, `position_values`, `cash_balances`, `reconciliations`.
  `snapshot_date` semantics become "statement EOD date" (document; keep existing UNIQUE keys).
- **`app/repository/positions.py`** (`record_reconciliation`): change from unbounded append to
  **delete-and-replace for the statement date** (`DELETE FROM reconciliations WHERE
  snapshot_date = ?` then insert), bounding growth and matching the partition model.
- **Read paths** (`latest_position_values`, latest cash/positions): unchanged — they already
  select `MAX(snapshot_date)`, which is now correct.

---

## Part 2 — Evidence store

### Goal
Retain every ingested raw Flex XML (manual upload or scheduled run), committed in the same
transaction as the derived data, so every dashboard number is traceable to its exact source
document and the derived state is fully reproducible from stored evidence.

### Why in SQLite (not files on disk)
- **Atomicity:** evidence and the data it produced commit or fail together (no sync gap in the
  audit trail) — matches the platform's provenance philosophy.
- **Capacity is a non-issue:** ~700KB/XML × 2 logins/day ≈ 1.4MB/day; gzip-compressed to
  ~80KB each → ~40MB/decade. Trivial for SQLite (disk-bound, 281TB ceiling).
- **One-file backup:** the whole system (data + evidence) stays a single `.sqlite3` file to
  copy.
- **Resilience:** IBKR won't reliably re-serve historical statements (rate limits, 1001). The
  evidence store becomes a **more durable source of truth than IBKR** — derived state can be
  rebuilt by replaying stored XML rather than re-fetching.

### Table
`app/repository/db.py` (same `SCHEMA_VERSION` bump):
```sql
CREATE TABLE IF NOT EXISTS evidence_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,        -- sha256 of raw xml
    source TEXT NOT NULL,                      -- 'login1' | 'login2' | 'manual_login1' ...
    ingest_kind TEXT NOT NULL,                 -- 'scheduled' | 'manual'
    statement_to_date TEXT,                    -- parsed toDate (statement EOD)
    statement_generated_at TEXT,               -- parsed whenGenerated
    ingested_at TEXT NOT NULL,                 -- datetime.now()
    byte_size INTEGER NOT NULL,                -- compressed size
    raw_size INTEGER NOT NULL,                 -- uncompressed size
    raw_xml_gzip BLOB NOT NULL                 -- gzip-compressed xml
);
```
Add `"evidence_store"` to the `TABLES` drop list.

### Dedup by content hash
- Compute `content_hash = sha256(xml_text)` before storing.
- If a row with that hash already exists → **do not store a second blob**; the statement is
  byte-identical to a prior ingest (IBKR returned the same data). Record the run/status as
  "same evidence as <existing id/hash>" and reference the existing row.
- Store-once / reference-many: re-running against an unchanged statement doesn't duplicate the
  700KB.

### Link snapshots to evidence (traceability)
- Add `content_hash TEXT` (or `evidence_id`) to `positions`, `position_values`,
  `cash_balances`, `reconciliations` — the hash of the XML that produced each row.
- Now every number traces to its source document; and the hash answers "did two runs pull the
  same statement?" in one query.

### Repository
`app/repository/evidence.py` (new):
- `store_evidence(conn, xml_text, source, ingest_kind, to_date, generated_at) -> (evidence_id,
  content_hash, was_new: bool)`: gzip, hash, insert-or-reference. Returns whether it was newly
  stored.
- `get_evidence(conn, content_hash|id) -> xml_text` (decompress on read).
- `list_evidence(conn, ...)`: metadata rows (no blob) for browsing.

### Wire into ingest
`app/routes/dashboard.py` (`_ingest_flex_xml`, inside the existing `with transaction(conn)`):
- Parse `to_date` / `when_generated` (Part 1).
- Call `store_evidence(...)` with the raw `xml_text`, before/alongside the derived writes, so
  it commits atomically. Pass the returned `content_hash` down to the snapshot upserts for the
  linkage column.
- Include `was_new` and same-statement info in the run status message.

---

## Privacy / ops
- Evidence (and the DB) contain account IDs/balances — keep in the `.gitignore`'d `data/`
  folder (already ignored). Never commit.
- **Backup habit** (note, not code): once daily history accumulates, the `.sqlite3` file holds
  irreplaceable forward-only data; periodically copy it off-machine. The evidence store makes
  the single-file backup even more valuable (it *is* the recoverable source of truth).
- **Schema-bump caveat:** a `SCHEMA_VERSION` bump currently drops all tables — including
  evidence. Before relying on evidence for recovery, this should become migrate-not-drop, or
  back up before schema changes. (Flag for a later hardening; out of scope here.)

---

## Tests
- **Statement date parse:** `toDate=20260731`→`2026-07-31`; `whenGenerated=20260802;172853`→
  `2026-08-02T17:28:53`.
- **Partition key:** ingest with `toDate=T` writes snapshots under T, not `today()`. Two
  ingests same `toDate` → one partition, upserted, latest wins.
- **History retained:** two different `toDate` → two partitions; `MAX` read returns later; both
  present.
- **Reconciliations replace, not accumulate.**
- **Evidence store:** first ingest stores a gzipped blob (`was_new=True`); re-ingesting
  byte-identical XML → `was_new=False`, no duplicate blob, run status notes same evidence.
  `get_evidence` round-trips (decompressed == original).
- **Linkage:** snapshot rows carry the correct `content_hash`; the hash matches the evidence
  row.
- **Anomaly regression:** day-1 statement (P1, toDate=T1) then a day-2 run returning the SAME
  statement → same partition T1, same content_hash, dashboard shows P1 under T1 (correct), CAD
  PnL non-zero (from the good data), status "same statement". No stale zero-PnL partition under
  a drifted run-date.

## Verification
- Run twice against the same export → one partition, one evidence row, idempotent, status notes
  no new statement.
- Run against two different-EOD statements → two history points, two evidence rows, dashboard
  shows the later.
- From the DB alone, for any snapshot row: its EOD (`snapshot_date`), IBKR generation time
  (`statement_generated_at`), our ingest time (`ingested_at`), and the exact source XML (via
  `content_hash` → `evidence_store`).

## Why this is the scheduler keystone
The daily job would otherwise reproduce the stale-partition anomaly whenever it runs before
IBKR generates a new statement. Statement-date partitioning makes it idempotent and
run-timing-independent; the evidence store + `whenGenerated` make every run auditable and the
derived state reproducible. Do this before deploying the scheduler.
