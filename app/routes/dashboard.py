from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings, get_settings
from app.db import connect, init_db, reset_db, transaction
from app.ingestion.cibc_csv import parse_cibc_transactions
from app.ingestion.ibkr_flex import (
    FlexClient,
    parse_flex_cash_reports,
    parse_flex_position_values,
    parse_flex_positions,
    parse_flex_transactions,
    summarize_flex_xml,
    today_snapshot_date,
)
from app.ingestion.ibkr_gateway import GatewayAuthError, GatewayClient, current_fx_mark
from app.ingestion.reference_data import YFinanceProvider
from app.repository.cash import record_cash_reconciliation, upsert_cash_balances
from app.repository.observations import append_fx_rate, append_price, instruments_for_price_refresh
from app.repository.position_values import upsert_position_values
from app.repository.positions import rebuild_derived_state, record_reconciliation
from app.repository.runs import record_run
from app.repository.transactions import append_transactions
from app.services.instruments import enrich_missing_instruments
from app.services.valuation import build_dashboard_data


router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _short_id(value: str) -> str:
    if len(value) <= 4:
        return value
    return "...%s" % value[-4:]


def _login_error(login_name: str, query_id: str, exc: Exception) -> RuntimeError:
    return RuntimeError(
        "IBKR Flex %s failed for query_id %s: %s"
        % (login_name, _short_id(query_id), exc)
    )


def _ingest_flex_xml(conn, xml_text: str, source_key: str, snapshot_date: str):
    summary = summarize_flex_xml(xml_text)
    parsed_transactions = parse_flex_transactions(xml_text, source="ibkr_flex_%s" % source_key)
    parsed_positions = parse_flex_positions(xml_text)
    parsed_position_values = parse_flex_position_values(xml_text)
    parsed_cash_reports = parse_flex_cash_reports(xml_text)
    inserted = append_transactions(conn, parsed_transactions)
    position_values = upsert_position_values(
        conn,
        parsed_position_values,
        snapshot_date,
        "IBKR Flex %s values" % source_key,
    )
    lots, positions = rebuild_derived_state(conn, snapshot_date)
    reconciled = record_reconciliation(
        conn,
        parsed_positions,
        snapshot_date,
        "IBKR Flex %s positions" % source_key,
    )
    cash_reconciled = record_cash_reconciliation(
        conn,
        parsed_cash_reports,
        snapshot_date,
        "IBKR Flex %s cash reports" % source_key,
    )
    cash_balances = upsert_cash_balances(
        conn,
        parsed_cash_reports,
        snapshot_date,
        "IBKR Flex %s cash reports" % source_key,
    )
    return {
        "summary": summary,
        "transactions": len(parsed_transactions),
        "positions": len(parsed_positions),
        "position_values": len(parsed_position_values),
        "cash_reports": len(parsed_cash_reports),
        "inserted": inserted,
        "stored_position_values": position_values,
        "lots": lots,
        "derived_positions": positions,
        "reconciled": reconciled,
        "cash_reconciled": cash_reconciled,
        "cash_balances": cash_balances,
    }


def _section_summary(label: str, summary) -> str:
    return "%s sections: trades=%s executions=%s cash_txns=%s open_positions=%s cash_reports=%s" % (
        label,
        summary["Trade"],
        summary["Execution"],
        summary["CashTransaction"],
        summary["OpenPosition"] + summary["Position"],
        summary["CashReportCurrency"] or summary["CashReport"],
    )


def settings_dep() -> Settings:
    return get_settings()


def db_conn(settings: Settings = Depends(settings_dep)):
    conn = connect(settings.database_path)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn=Depends(db_conn)):
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "data": build_dashboard_data(conn)},
    )


@router.get("/health")
def health(conn=Depends(db_conn)):
    row = conn.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()
    txn_row = conn.execute("SELECT COUNT(*) AS count FROM transactions").fetchone()
    return {"ok": True, "accounts": row["count"], "transactions": txn_row["count"]}


@router.post("/dev/reset-db")
def reset_database(conn=Depends(db_conn)):
    reset_db(conn)
    record_run(conn, "dev_reset", "success", "Reset local development database.")
    conn.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/refresh/transactions")
def refresh_transactions(settings: Settings = Depends(settings_dep), conn=Depends(db_conn)):
    if not settings.flex_logins:
        with transaction(conn):
            record_run(conn, "transactions", "skipped", "No IBKR Flex credentials configured.")
        return RedirectResponse("/", status_code=303)

    client = FlexClient(settings.flex_base_url)
    snapshot_date = today_snapshot_date()
    inserted = 0
    reconciled = 0
    cash_reconciled = 0
    cash_balances = 0
    position_values = 0
    parsed_transactions_count = 0
    parsed_positions_count = 0
    parsed_position_values_count = 0
    parsed_cash_reports_count = 0
    section_counts = []
    failures = []
    refreshed_logins = 0
    lots = 0
    positions = 0
    for login_name, login in settings.flex_logins.items():
        try:
            xml_text = client.fetch_statement(login.token, login.query_id)
            with transaction(conn):
                result = _ingest_flex_xml(conn, xml_text, login_name, snapshot_date)
            section_counts.append(_section_summary(login_name, result["summary"]))
            inserted += result["inserted"]
            position_values += result["stored_position_values"]
            lots = result["lots"]
            positions = result["derived_positions"]
            reconciled += result["reconciled"]
            cash_reconciled += result["cash_reconciled"]
            cash_balances += result["cash_balances"]
            parsed_transactions_count += result["transactions"]
            parsed_positions_count += result["positions"]
            parsed_position_values_count += result["position_values"]
            parsed_cash_reports_count += result["cash_reports"]
            refreshed_logins += 1
        except Exception as exc:
            failures.append(str(_login_error(login_name, login.query_id, exc)))

    status = "success"
    hint = ""
    if refreshed_logins == 0:
        status = "failed"
        hint = " No Flex logins refreshed."
    elif failures:
        status = "partial_success"
        hint = " Some Flex logins failed; previously stored data for failed logins was left unchanged."
    elif parsed_transactions_count == 0 and parsed_positions_count > 0:
        status = "needs_transaction_history"
        hint = " Broker positions were found, but no trades/cash flows were parsed; expand the Flex query sections/date range."
    failure_text = " Failures: %s" % " | ".join(failures) if failures else ""
    with transaction(conn):
        record_run(
            conn,
            "transactions",
            status,
            (
                "Refreshed %s/%s Flex logins. Parsed %s transactions/%s broker positions/%s position values/%s cash reports; "
                "inserted %s transactions; rebuilt %s lots/%s positions; stored %s position values/%s cash balances; "
                "reconciled %s position rows/%s cash checks.%s %s%s"
            )
            % (
                refreshed_logins,
                len(settings.flex_logins),
                parsed_transactions_count,
                parsed_positions_count,
                parsed_position_values_count,
                parsed_cash_reports_count,
                inserted,
                lots,
                positions,
                position_values,
                cash_balances,
                reconciled,
                cash_reconciled,
                hint,
                " | ".join(section_counts),
                failure_text,
            ),
        )
    return RedirectResponse("/", status_code=303)


@router.post("/upload/cibc")
async def upload_cibc(file: UploadFile = File(...), conn=Depends(db_conn)):
    try:
        content = (await file.read()).decode("utf-8-sig")
        transactions = parse_cibc_transactions(content)
        with transaction(conn):
            inserted = append_transactions(conn, transactions)
            lots, positions = rebuild_derived_state(conn, today_snapshot_date())
            record_run(
                conn,
                "cibc_csv",
                "success",
                "Inserted %s transactions; rebuilt %s lots/%s positions." % (inserted, lots, positions),
            )
    except Exception as exc:
        with transaction(conn):
            record_run(conn, "cibc_csv", "failed", str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/upload/flex")
async def upload_flex(
    file: UploadFile = File(...),
    source_label: str = Form("manual"),
    conn=Depends(db_conn),
):
    safe_label = "".join(ch.lower() if ch.isalnum() else "_" for ch in source_label).strip("_") or "manual"
    source_key = "manual_%s" % safe_label
    try:
        xml_text = (await file.read()).decode("utf-8-sig")
        snapshot_date = today_snapshot_date()
        with transaction(conn):
            result = _ingest_flex_xml(conn, xml_text, source_key, snapshot_date)
            record_run(
                conn,
                "flex_xml_upload",
                "success",
                (
                    "Uploaded %s. Parsed %s transactions/%s broker positions/%s position values/%s cash reports; "
                    "inserted %s transactions; rebuilt %s lots/%s positions; stored %s position values/%s cash balances; "
                    "reconciled %s position rows/%s cash checks. %s"
                )
                % (
                    source_key,
                    result["transactions"],
                    result["positions"],
                    result["position_values"],
                    result["cash_reports"],
                    result["inserted"],
                    result["lots"],
                    result["derived_positions"],
                    result["stored_position_values"],
                    result["cash_balances"],
                    result["reconciled"],
                    result["cash_reconciled"],
                    _section_summary(source_key, result["summary"]),
                ),
            )
    except Exception as exc:
        with transaction(conn):
            record_run(conn, "flex_xml_upload", "failed", str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/refresh/prices")
def refresh_prices(settings: Settings = Depends(settings_dep), conn=Depends(db_conn)):
    client = GatewayClient(settings.gateway_base_url)
    priced = 0
    try:
        rows = instruments_for_price_refresh(conn)
        with transaction(conn):
            for row in rows:
                mark = client.fetch_eod_price(str(row["conid"]), row["currency"])
                append_price(conn, row["id"], mark.as_of, mark.price, mark.currency, "ibkr_history")
                priced += 1
            fx_mark = current_fx_mark(settings.manual_usdcad_rate)
            if fx_mark is not None:
                append_fx_rate(conn, "USDCAD", fx_mark.as_of, fx_mark.price, "manual_env")
            record_run(
                conn,
                "prices",
                "success",
                "Appended %s price rows%s."
                % (priced, " and USDCAD FX" if fx_mark is not None else ""),
            )
    except GatewayAuthError as exc:
        with transaction(conn):
            record_run(conn, "prices", "auth_required", str(exc))
    except Exception as exc:
        with transaction(conn):
            record_run(conn, "prices", "failed", str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/refresh/reference")
def refresh_reference(conn=Depends(db_conn)):
    try:
        with transaction(conn):
            count = enrich_missing_instruments(conn, YFinanceProvider())
            record_run(conn, "reference", "success", "Enriched %s instruments." % count)
    except Exception as exc:
        with transaction(conn):
            record_run(conn, "reference", "failed", str(exc))
    return RedirectResponse("/", status_code=303)
