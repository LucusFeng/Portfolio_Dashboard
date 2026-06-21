import datetime as dt

from fastapi import FastAPI

from app.config import get_settings
from app.db import connect, init_db, transaction
from app.repository.observations import append_fx_rate
from app.routes.dashboard import router as dashboard_router


app = FastAPI(title="Personal Investment Ecosystem")
app.include_router(dashboard_router)


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
