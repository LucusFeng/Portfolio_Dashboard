# SQLite Guide

This guide shows how to inspect the local portfolio database from Terminal and how to retrieve
raw Flex XML from the `evidence_store` table.

The local database is configured by `.env`. In normal development it is:

```text
data/portfolio.sqlite3
```

The database contains account IDs, balances, positions, and raw Flex XML evidence. Treat it as
private data and do not commit it to Git.

## Open The Database

From the project root:

```bash
cd /Users/lukefeng/Code/Portfolio_Dashboard
sqlite3 data/portfolio.sqlite3
```

Inside the SQLite prompt, make output easier to read:

```sql
.headers on
.mode column
```

List all tables:

```sql
.tables
```

Show a table's schema:

```sql
.schema evidence_store
```

Exit SQLite:

```sql
.quit
```

## Useful Queries

### Latest ingest runs

```sql
SELECT id, kind, status, message, started_at, finished_at
FROM ingestion_runs
ORDER BY id DESC
LIMIT 10;
```

### Evidence metadata

This shows what XML statements are stored without printing the compressed blob.

```sql
SELECT
  id,
  source,
  ingest_kind,
  statement_to_date,
  statement_generated_at,
  ingested_at,
  raw_size,
  byte_size,
  substr(content_hash, 1, 12) AS hash_prefix
FROM evidence_store
ORDER BY ingested_at DESC, id DESC;
```

### Position values by statement date and evidence hash

```sql
SELECT
  snapshot_date,
  source,
  statement_generated_at,
  substr(content_hash, 1, 12) AS hash_prefix,
  COUNT(*) AS rows,
  ROUND(SUM(value_base), 2) AS value_cad
FROM position_values
GROUP BY snapshot_date, source, statement_generated_at, content_hash
ORDER BY snapshot_date DESC, source;
```

### Cash balances by statement date

```sql
SELECT
  c.snapshot_date,
  a.label AS account,
  c.currency,
  c.ending_cash,
  c.source,
  substr(c.content_hash, 1, 12) AS hash_prefix
FROM cash_balances c
JOIN accounts a ON a.id = c.account_id
ORDER BY c.snapshot_date DESC, a.label, c.currency;
```

### Reconciliation warnings

```sql
SELECT
  r.snapshot_date,
  a.label AS account,
  i.symbol,
  r.broker_quantity,
  r.derived_quantity,
  r.difference,
  r.status,
  substr(r.content_hash, 1, 12) AS hash_prefix
FROM reconciliations r
JOIN accounts a ON a.id = r.account_id
JOIN instruments i ON i.id = r.instrument_id
WHERE r.status != 'ok'
ORDER BY ABS(r.difference) DESC;
```

### Transactions linked to evidence

```sql
SELECT
  t.txn_date,
  a.label AS account,
  COALESCE(i.symbol, 'CASH') AS symbol,
  t.txn_type,
  t.quantity,
  t.amount,
  t.currency,
  t.source,
  substr(t.content_hash, 1, 12) AS hash_prefix
FROM transactions t
JOIN accounts a ON a.id = t.account_id
LEFT JOIN instruments i ON i.id = t.instrument_id
ORDER BY t.txn_date DESC, t.id DESC
LIMIT 50;
```

### Find rows produced by one evidence record

First get the hash:

```sql
SELECT id, content_hash
FROM evidence_store
ORDER BY id DESC;
```

Then use the hash in linked tables:

```sql
SELECT COUNT(*) AS transactions
FROM transactions
WHERE content_hash = 'paste_full_hash_here';

SELECT COUNT(*) AS position_values
FROM position_values
WHERE content_hash = 'paste_full_hash_here';

SELECT COUNT(*) AS cash_balances
FROM cash_balances
WHERE content_hash = 'paste_full_hash_here';
```

## Why `raw_xml_gzip` Looks Unreadable

The `evidence_store.raw_xml_gzip` column stores the actual Flex XML, but it is gzip-compressed
binary data. Most database viewers show it as a BLOB size, such as `53.1 KB`, rather than text.

Use the metadata columns for regular inspection:

```sql
SELECT id, source, statement_to_date, raw_size, byte_size, content_hash
FROM evidence_store;
```

Use Python when you need the readable XML.

## Retrieve Raw XML From Evidence Store

Activate the project environment:

```bash
cd /Users/lukefeng/Code/Portfolio_Dashboard
source .venv/bin/activate
```

Print XML by evidence ID:

```bash
python - <<'PY'
from app.db import connect
from app.repository.evidence import get_evidence

conn = connect("data/portfolio.sqlite3")
xml_text = get_evidence(conn, evidence_id=1)
print(xml_text[:2000])
conn.close()
PY
```

Print XML by content hash:

```bash
python - <<'PY'
from app.db import connect
from app.repository.evidence import get_evidence

content_hash = "paste_full_hash_here"

conn = connect("data/portfolio.sqlite3")
xml_text = get_evidence(conn, content_hash=content_hash)
print(xml_text[:2000])
conn.close()
PY
```

Export one evidence XML to a local ignored file:

```bash
python - <<'PY'
from pathlib import Path

from app.db import connect
from app.repository.evidence import get_evidence

evidence_id = 1
output_path = Path("data/evidence_%s.xml" % evidence_id)

conn = connect("data/portfolio.sqlite3")
xml_text = get_evidence(conn, evidence_id=evidence_id)
output_path.write_text(xml_text, encoding="utf-8")
conn.close()

print(output_path)
PY
```

The `data/` folder is gitignored, so exported XML stays local.

## One-Line Queries From Terminal

You can also run quick queries without entering the SQLite prompt:

```bash
sqlite3 -header -column data/portfolio.sqlite3 \
  "SELECT id, source, statement_to_date, raw_size, byte_size FROM evidence_store;"
```

Another useful one-liner:

```bash
sqlite3 -header -column data/portfolio.sqlite3 \
  "SELECT snapshot_date, source, COUNT(*) AS rows, ROUND(SUM(value_base), 2) AS cad FROM position_values GROUP BY snapshot_date, source;"
```

## Common Problems

### `sqlite3: command not found`

Install SQLite command-line tools or use a DB viewer. macOS usually includes `sqlite3` by
default.

### `no such table: evidence_store`

Make sure the app has run after `SCHEMA_VERSION = 8`, or restart the FastAPI app so the schema
initialization runs.

```bash
source .venv/bin/activate
python -m uvicorn app.main:app --reload
```

### Querying `raw_xml_gzip` prints unreadable characters

That is expected. It is compressed binary XML. Use `get_evidence()` from Python to decompress it.

### The DB looks empty after a schema change

During development, schema version bumps reset app tables. This is expected for now. Before the
database becomes a durable production archive, schema changes should move to migrations or the
database should be backed up before bumping the schema.
