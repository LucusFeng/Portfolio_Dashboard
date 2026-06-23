from pathlib import Path

import pytest

from app.ingestion.ibkr_flex import FlexClient, parse_flex_positions, parse_flex_transactions
from app.services.flex import parse_flex_xml


def test_parse_flex_xml_filters_supported_positions_and_cash():
    xml_text = Path("tests/fixtures/sample_flex.xml").read_text()

    positions = parse_flex_xml(xml_text)

    assert [position.symbol for position in positions] == ["AAPL", "XIC", "CASH:USD"]
    assert positions[0].account_label == "RRSP"
    assert positions[0].quantity == 10
    assert positions[0].avg_cost == 150.0
    assert positions[0].conid == "265598"
    assert positions[2].asset_class == "CASH"
    assert positions[2].quantity == 1250.50


def test_parse_flex_transactions_normalizes_trades_and_cash_flows():
    xml_text = Path("tests/fixtures/sample_flex.xml").read_text()

    transactions = parse_flex_transactions(xml_text)

    assert [txn.txn_type for txn in transactions] == ["BUY", "SELL", "BUY", "DEPOSIT", "DIVIDEND"]
    assert transactions[0].instrument.symbol == "AAPL"
    assert transactions[0].quantity == 10
    assert transactions[0].amount == -1501
    assert transactions[1].txn_type == "SELL"
    assert transactions[1].amount == 700


def test_parse_flex_positions_keeps_reconciliation_shape():
    xml_text = Path("tests/fixtures/sample_flex.xml").read_text()

    positions = parse_flex_positions(xml_text)

    assert positions[0].asset_class == "EQUITY"
    assert positions[1].asset_class == "ETF"


def test_reference_code_error_includes_ibkr_response_details():
    xml_text = """
    <FlexStatementResponse>
      <Status>Fail</Status>
      <ErrorCode>1012</ErrorCode>
      <ErrorMessage>Invalid query ID</ErrorMessage>
    </FlexStatementResponse>
    """

    with pytest.raises(RuntimeError) as exc:
        FlexClient._reference_code(xml_text)

    assert "Status=Fail" in str(exc.value)
    assert "ErrorCode=1012" in str(exc.value)
    assert "Invalid query ID" in str(exc.value)
