import sqlite3
from pathlib import Path

from app.config import FlexLoginConfig, Settings
from app.db import init_db
import app.routes.dashboard as dashboard_routes
from app.routes.dashboard import _login_error, _short_id


def test_short_id_masks_query_id_for_diagnostics():
    assert _short_id("123456789") == "...6789"
    assert _short_id("1234") == "1234"


def test_login_error_includes_login_name_and_masked_query_id():
    error = _login_error("login2", "987654321", RuntimeError("Statement could not be generated"))

    message = str(error)

    assert "login2 failed" in message
    assert "...4321" in message
    assert "987654321" not in message
    assert "Statement could not be generated" in message


def test_refresh_transactions_continues_after_one_login_fails(monkeypatch):
    class FakeFlexClient:
        def __init__(self, base_url, **kwargs):
            self.base_url = base_url

        def fetch_statement(self, token, query_id):
            if query_id == "bad-query-0072":
                raise RuntimeError("Statement could not be generated")
            return Path("tests/fixtures/sample_flex.xml").read_text()

    monkeypatch.setattr(dashboard_routes, "FlexClient", FakeFlexClient)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    settings = Settings(
        database_path=":memory:",
        gateway_base_url="https://localhost:5000/v1/api",
        flex_base_url="https://example.test",
        flex_logins={
            "login1": FlexLoginConfig("login1", "token1", "bad-query-0072"),
            "login2": FlexLoginConfig("login2", "token2", "good-query-9557"),
        },
        manual_usdcad_rate=None,
        flex_inter_login_delay_seconds=0,
        flex_statement_poll_attempts=1,
        flex_statement_poll_interval_seconds=0,
    )

    dashboard_routes.refresh_transactions(settings=settings, conn=conn)

    run = conn.execute("SELECT status, message FROM ingestion_runs ORDER BY id DESC LIMIT 1").fetchone()
    source = conn.execute("SELECT source, COUNT(*) AS count FROM position_values GROUP BY source").fetchone()

    assert run["status"] == "partial_success"
    assert "Refreshed 1/2 Flex logins" in run["message"]
    assert "login1 failed" in run["message"]
    assert "...0072" in run["message"]
    assert "login2 sections" in run["message"]
    assert source["source"] == "IBKR Flex login2 values"
    assert source["count"] == 2


def test_refresh_single_login_only_calls_requested_login(monkeypatch):
    calls = []

    class FakeFlexClient:
        def __init__(self, base_url, **kwargs):
            self.base_url = base_url

        def fetch_statement(self, token, query_id):
            calls.append((token, query_id))
            return Path("tests/fixtures/sample_flex.xml").read_text()

    monkeypatch.setattr(dashboard_routes, "FlexClient", FakeFlexClient)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    settings = Settings(
        database_path=":memory:",
        gateway_base_url="https://localhost:5000/v1/api",
        flex_base_url="https://example.test",
        flex_logins={
            "login1": FlexLoginConfig("login1", "token1", "query1-0072"),
            "login2": FlexLoginConfig("login2", "token2", "query2-9557"),
        },
        manual_usdcad_rate=None,
        flex_inter_login_delay_seconds=0,
        flex_statement_poll_attempts=1,
        flex_statement_poll_interval_seconds=0,
    )

    dashboard_routes.refresh_transactions_for_login("login2", settings=settings, conn=conn)

    run = conn.execute("SELECT status, message FROM ingestion_runs ORDER BY id DESC LIMIT 1").fetchone()

    assert calls == [("token2", "query2-9557")]
    assert run["status"] == "success"
    assert "Refreshed 1/1 Flex logins" in run["message"]
    assert "login2 sections" in run["message"]
    assert "login1 sections" not in run["message"]


def test_refresh_single_login_reports_unconfigured_login():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    settings = Settings(
        database_path=":memory:",
        gateway_base_url="https://localhost:5000/v1/api",
        flex_base_url="https://example.test",
        flex_logins={
            "login1": FlexLoginConfig("login1", "token1", "query1-0072"),
        },
        manual_usdcad_rate=None,
        flex_inter_login_delay_seconds=0,
        flex_statement_poll_attempts=1,
        flex_statement_poll_interval_seconds=0,
    )

    dashboard_routes.refresh_transactions_for_login("login2", settings=settings, conn=conn)

    run = conn.execute("SELECT status, message FROM ingestion_runs ORDER BY id DESC LIMIT 1").fetchone()

    assert run["status"] == "failed"
    assert "No configured Flex login matched" in run["message"]
    assert "login2" in run["message"]


def test_manual_flex_xml_ingestion_helper_uses_same_pipeline():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    xml_text = Path("tests/fixtures/sample_flex.xml").read_text()

    result = dashboard_routes._ingest_flex_xml(conn, xml_text, "manual_login1", "2026-08-02")

    run_source = conn.execute("SELECT source, COUNT(*) AS count FROM position_values GROUP BY source").fetchone()
    lots = conn.execute("SELECT COUNT(*) AS count FROM lots").fetchone()

    assert result["transactions"] == 10
    assert result["positions"] == 2
    assert result["position_values"] == 2
    assert result["cash_reports"] == 2
    assert run_source["source"] == "IBKR Flex manual_login1 values"
    assert run_source["count"] == 2
    assert lots["count"] == 2


def test_reset_database_clears_dev_data_and_records_run():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    xml_text = Path("tests/fixtures/sample_flex.xml").read_text()
    dashboard_routes._ingest_flex_xml(conn, xml_text, "manual_login1", "2026-08-02")

    dashboard_routes.reset_database(conn=conn)

    accounts = conn.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()
    positions = conn.execute("SELECT COUNT(*) AS count FROM position_values").fetchone()
    run = conn.execute("SELECT kind, status, message FROM ingestion_runs ORDER BY id DESC LIMIT 1").fetchone()

    assert accounts["count"] == 0
    assert positions["count"] == 0
    assert run["kind"] == "dev_reset"
    assert run["status"] == "success"
    assert "Reset local development database" in run["message"]
