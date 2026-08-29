import gzip
import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EvidenceResult:
    evidence_id: int
    content_hash: str
    was_new: bool


def _hash_xml(xml_text: str) -> str:
    return hashlib.sha256(xml_text.encode("utf-8")).hexdigest()


def store_evidence(
    conn: sqlite3.Connection,
    xml_text: str,
    source: str,
    ingest_kind: str,
    statement_to_date: Optional[str],
    statement_generated_at: Optional[str],
    ingested_at: str,
) -> EvidenceResult:
    content_hash = _hash_xml(xml_text)
    existing = conn.execute(
        "SELECT id FROM evidence_store WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    if existing is not None:
        return EvidenceResult(int(existing["id"]), content_hash, False)

    raw_bytes = xml_text.encode("utf-8")
    compressed = gzip.compress(raw_bytes)
    cursor = conn.execute(
        """
        INSERT INTO evidence_store
            (content_hash, source, ingest_kind, statement_to_date, statement_generated_at,
             ingested_at, byte_size, raw_size, raw_xml_gzip)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            content_hash,
            source,
            ingest_kind,
            statement_to_date,
            statement_generated_at,
            ingested_at,
            len(compressed),
            len(raw_bytes),
            compressed,
        ),
    )
    return EvidenceResult(int(cursor.lastrowid), content_hash, True)


def get_evidence(conn: sqlite3.Connection, *, content_hash: Optional[str] = None, evidence_id: Optional[int] = None) -> str:
    if content_hash is None and evidence_id is None:
        raise ValueError("content_hash or evidence_id is required")
    if evidence_id is not None:
        row = conn.execute(
            "SELECT raw_xml_gzip FROM evidence_store WHERE id = ?",
            (evidence_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT raw_xml_gzip FROM evidence_store WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
    if row is None:
        raise KeyError("evidence not found")
    return gzip.decompress(row["raw_xml_gzip"]).decode("utf-8")


def list_evidence(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT id, content_hash, source, ingest_kind, statement_to_date,
               statement_generated_at, ingested_at, byte_size, raw_size, created_at
        FROM evidence_store
        ORDER BY ingested_at DESC, id DESC
        """
    ).fetchall()
