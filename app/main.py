import datetime as dt
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings, get_settings
from app.db import connect, init_db, transaction
from app.services.flex import FlexClient, parse_flex_xml, today_snapshot_date
from app.services.pricing import GatewayAuthError, GatewayClient, current_fx_mark
from app.services.repository import append_fx_rate, append_positions, append_price
from app.services.valuation import build_dashboard_data


app = FastAPI(title="Portfolio Consolidation Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def settings_dep() -> Settings:
    return get_settings()


def db_conn(settings: Settings = Depends(settings_dep)):
    conn = connect(settings.database_path)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def record_run(conn, kind: str, status: str, message: str) -> None:
    conn.execute(
        """
        INSERT INTO ingestion_runs (kind, status, message, finished_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (kind, status, message),
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn=Depends(db_conn)):
    data = build_dashboard_data(conn)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "data": data,
        },
    )


@app.get("/health")
def health(conn=Depends(db_conn)):
    row = conn.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()
    return {"ok": True, "accounts": row["count"]}


@app.post("/refresh/positions")
def refresh_positions(settings: Settings = Depends(settings_dep), conn=Depends(db_conn)):
    if not settings.flex_logins:
        with transaction(conn):
            record_run(conn, "positions", "skipped", "No IBKR Flex credentials configured.")
        return RedirectResponse("/", status_code=303)

    client = FlexClient(settings.flex_base_url)
    total = 0
    snapshot_date = today_snapshot_date()
    try:
        with transaction(conn):
            for login_name, login in settings.flex_logins.items():
                xml_text = client.fetch_statement(login.token, login.query_id)
                positions = parse_flex_xml(xml_text)
                total += append_positions(
                    conn,
                    positions,
                    snapshot_date=snapshot_date,
                    source="IBKR Flex %s" % login_name,
                )
            record_run(conn, "positions", "success", "Appended %s position rows." % total)
    except Exception as exc:
        with transaction(conn):
            record_run(conn, "positions", "failed", str(exc))
    return RedirectResponse("/", status_code=303)


@app.post("/refresh/prices")
def refresh_prices(settings: Settings = Depends(settings_dep), conn=Depends(db_conn)):
    client = GatewayClient(settings.gateway_base_url)
    priced = 0
    try:
        rows = conn.execute(
            """
            SELECT id, conid, currency, asset_class
            FROM instruments
            WHERE asset_class != 'CASH' AND conid IS NOT NULL AND conid != ''
            ORDER BY symbol
            """
        ).fetchall()
        with transaction(conn):
            for row in rows:
                mark = client.fetch_eod_price(str(row["conid"]), row["currency"])
                append_price(conn, row["id"], mark.as_of, mark.price, mark.currency, "IBKR Gateway history")
                priced += 1

            fx_mark = current_fx_mark(settings.manual_usdcad_rate)
            if fx_mark is not None:
                append_fx_rate(conn, "USDCAD", fx_mark.as_of, fx_mark.price, "manual env")
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


@app.on_event("startup")
def startup_init() -> None:
    settings = get_settings()
    conn = connect(settings.database_path)
    try:
        init_db(conn)
        if settings.manual_usdcad_rate is not None:
            with transaction(conn):
                append_fx_rate(
                    conn,
                    "USDCAD",
                    dt.datetime.utcnow().isoformat(timespec="seconds"),
                    settings.manual_usdcad_rate,
                    "manual env",
                )
    finally:
        conn.close()
