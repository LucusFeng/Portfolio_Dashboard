import sqlite3
import datetime as dt
from typing import Optional


def record_run(conn: sqlite3.Connection, kind: str, status: str, message: str) -> None:
    conn.execute(
        """
        INSERT INTO ingestion_runs (kind, status, message, finished_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (kind, status, message),
    )


def latest_run_message(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        """
        SELECT kind || ': ' || status || COALESCE(' - ' || message, '') AS message
        FROM ingestion_runs
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return row["message"] if row else None


def latest_run_time(conn: sqlite3.Connection, kind: str) -> Optional[dt.datetime]:
    row = conn.execute(
        """
        SELECT COALESCE(finished_at, started_at) AS run_time
        FROM ingestion_runs
        WHERE kind = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (kind,),
    ).fetchone()
    if row is None or not row["run_time"]:
        return None
    return dt.datetime.strptime(row["run_time"], "%Y-%m-%d %H:%M:%S")
