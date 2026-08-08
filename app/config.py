import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class FlexLoginConfig:
    name: str
    token: str
    query_id: str


@dataclass(frozen=True)
class Settings:
    database_path: str
    gateway_base_url: str
    flex_base_url: str
    flex_logins: Dict[str, FlexLoginConfig]
    manual_usdcad_rate: Optional[float]
    flex_inter_login_delay_seconds: float = 15.0
    flex_statement_poll_attempts: int = 36
    flex_statement_poll_interval_seconds: float = 5.0


def _optional_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return float(value)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return int(value)


def get_settings() -> Settings:
    load_dotenv()
    flex_logins = {}
    for login_name in ("LOGIN1", "LOGIN2"):
        token = os.getenv("IBKR_FLEX_%s_TOKEN" % login_name, "")
        query_id = os.getenv("IBKR_FLEX_%s_QUERY_ID" % login_name, "")
        if token and query_id:
            flex_logins[login_name.lower()] = FlexLoginConfig(
                name=login_name.lower(),
                token=token,
                query_id=query_id,
            )

    return Settings(
        database_path=os.getenv("DATABASE_PATH", "data/portfolio.sqlite3"),
        gateway_base_url=os.getenv("IBKR_GATEWAY_BASE_URL", "https://localhost:5000/v1/api"),
        flex_base_url=os.getenv(
            "IBKR_FLEX_BASE_URL",
            "https://gdcdyn.interactivebrokers.com/Universal/servlet",
        ),
        flex_logins=flex_logins,
        manual_usdcad_rate=_optional_float(os.getenv("MANUAL_USDCAD_RATE")),
        flex_inter_login_delay_seconds=_float_env("IBKR_FLEX_INTER_LOGIN_DELAY_SECONDS", 15.0),
        flex_statement_poll_attempts=_int_env("IBKR_FLEX_STATEMENT_POLL_ATTEMPTS", 36),
        flex_statement_poll_interval_seconds=_float_env("IBKR_FLEX_STATEMENT_POLL_INTERVAL_SECONDS", 5.0),
    )
