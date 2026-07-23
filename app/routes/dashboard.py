from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings, get_settings
from app.db import connect, init_db, transaction
from app.ingestion.cibc_csv import parse_cibc_transactions
from app.ingestion.ibkr_flex import (
    FlexClient,
    parse_flex_cash_reports,
    parse_flex_positions,
    parse_flex_transactions,
    summarize_flex_xml,
    today_snapshot_date,
)
from app.ingestion.ibkr_gateway import GatewayAuthError, GatewayClient, current_fx_mark
from app.ingestion.reference_data import YFinanceProvider
from app.repository.cash import record_cash_reconciliation, upsert_cash_balances
from app.repository.observations import append_fx_rate, append_price, instruments_for_price_refresh
from app.repository.positions import rebuild_derived_state, record_reconciliation
from app.repository.runs import record_run
from app.repository.transactions import append_transactions
from app.services.instruments import enrich_missing_instruments
from app.services.valuation import build_dashboard_data


router = APIRouter()
templates = Jinja2Templates(directory="templates")


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
    parsed_transactions_count = 0
    parsed_positions_count = 0
    parsed_cash_reports_count = 0
    section_counts = []
    lots = 0
    positions = 0
    try:
        with transaction(conn):
            for login_name, login in settings.flex_logins.items():
                xml_text = client.fetch_statement(login.token, login.query_id)
                summary = summarize_flex_xml(xml_text)
                section_counts.append(
                    "%s sections: trades=%s executions=%s cash_txns=%s open_positions=%s cash_reports=%s"
                    % (
                        login_name,
                        summary["Trade"],
                        summary["Execution"],
                        summary["CashTransaction"],
                        summary["OpenPosition"] + summary["Position"],
                        summary["CashReportCurrency"] or summary["CashReport"],
                    )
                )
                parsed_transactions = parse_flex_transactions(xml_text, source="ibkr_flex_%s" % login_name)
                parsed_positions = parse_flex_positions(xml_text)
                parsed_cash_reports = parse_flex_cash_reports(xml_text)
                parsed_transactions_count += len(parsed_transactions)
                parsed_positions_count += len(parsed_positions)
                parsed_cash_reports_count += len(parsed_cash_reports)
                inserted += append_transactions(conn, parsed_transactions)
                lots, positions = rebuild_derived_state(conn, snapshot_date)
                reconciled += record_reconciliation(
                    conn,
                    parsed_positions,
                    snapshot_date,
                    "IBKR Flex %s positions" % login_name,
                )
                cash_reconciled += record_cash_reconciliation(
                    conn,
                    parsed_cash_reports,
                    snapshot_date,
                    "IBKR Flex %s cash reports" % login_name,
                )
                cash_balances += upsert_cash_balances(
                    conn,
                    parsed_cash_reports,
                    snapshot_date,
                    "IBKR Flex %s cash reports" % login_name,
                )
            status = "success"
            hint = ""
            if parsed_transactions_count == 0 and parsed_positions_count > 0:
                status = "needs_transaction_history"
                hint = " Broker positions were found, but no trades/cash flows were parsed; expand the Flex query sections/date range."
            record_run(
                conn,
                "transactions",
                status,
                (
                    "Parsed %s transactions/%s broker positions/%s cash reports; inserted %s transactions; "
                    "rebuilt %s lots/%s positions; stored %s cash balances; reconciled %s position rows/%s cash checks.%s %s"
                )
                % (
                    parsed_transactions_count,
                    parsed_positions_count,
                    parsed_cash_reports_count,
                    inserted,
                    lots,
                    positions,
                    cash_balances,
                    reconciled,
                    cash_reconciled,
                    hint,
                    " | ".join(section_counts),
                ),
            )
    except Exception as exc:
        with transaction(conn):
            record_run(conn, "transactions", "failed", str(exc))
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
