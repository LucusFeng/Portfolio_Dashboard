from app.ingestion.ibkr_flex import (
    FlexClient,
    parse_flex_cash_report,
    parse_flex_cash_reports,
    parse_flex_positions,
    parse_flex_transactions,
    today_snapshot_date,
)
from app.models import ParsedCashReport, ParsedPosition, ParsedTransaction


def parse_flex_xml(xml_text: str):
    return parse_flex_positions(xml_text)
