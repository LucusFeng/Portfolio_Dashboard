from pathlib import Path

import pytest

import app.ingestion.ibkr_flex as flex_module
from app.ingestion.ibkr_flex import (
    FlexClient,
    _cash_type,
    _date,
    parse_flex_cash_reports,
    parse_flex_position_values,
    parse_flex_positions,
    parse_flex_transactions,
)
from app.services.flex import parse_flex_xml


def test_parse_flex_xml_filters_supported_positions_and_cash():
    xml_text = Path("tests/fixtures/sample_flex.xml").read_text()

    positions = parse_flex_xml(xml_text)

    assert [position.symbol for position in positions] == ["AAPL", "XIC"]
    assert positions[0].account_label == "RRSP"
    assert positions[0].quantity == 10
    assert positions[0].avg_cost == 150.0
    assert positions[0].conid == "265598"


def test_parse_flex_transactions_normalizes_trades_and_cash_flows():
    xml_text = Path("tests/fixtures/sample_flex.xml").read_text()

    transactions = parse_flex_transactions(xml_text)

    assert [txn.txn_type for txn in transactions] == [
        "BUY",
        "SELL",
        "BUY",
        "DEPOSIT",
        "DEPOSIT",
        "WITHDRAWAL",
        "DIVIDEND",
        "DIVIDEND",
        "FEE",
        "FEE",
    ]
    assert transactions[0].instrument.symbol == "AAPL"
    assert transactions[0].quantity == 10
    assert transactions[0].amount == -1501
    assert transactions[0].trade_cost == 1501
    assert transactions[0].commission == -1
    assert transactions[1].txn_type == "SELL"
    assert transactions[1].amount == 700
    assert all(txn.instrument is None or txn.instrument.asset_class != "CASH" for txn in transactions)
    assert all(txn.external_id != "FX1" for txn in transactions)
    assert transactions[4].txn_date == "2026-05-01"
    signed_contributions = sum(
        txn.amount for txn in transactions if txn.txn_type in {"DEPOSIT", "WITHDRAWAL"} and txn.currency == "CAD"
    )
    positive_only = sum(
        txn.amount
        for txn in transactions
        if txn.txn_type in {"DEPOSIT", "WITHDRAWAL"} and txn.currency == "CAD" and txn.amount > 0
    )
    assert signed_contributions == 28500
    assert positive_only == 29263.69


def test_parse_flex_positions_keeps_reconciliation_shape():
    xml_text = Path("tests/fixtures/sample_flex.xml").read_text()

    positions = parse_flex_positions(xml_text)

    assert positions[0].asset_class == "EQUITY"
    assert positions[1].asset_class == "ETF"
    assert all(position.asset_class != "CASH" for position in positions)


def test_parse_flex_position_values_reads_reported_values_directly():
    xml_text = Path("tests/fixtures/sample_flex.xml").read_text()

    values = parse_flex_position_values(xml_text)

    assert [value.symbol for value in values] == ["AAPL", "XIC"]
    assert all(value.asset_class != "CASH" for value in values)
    assert values[0].quantity == 10
    assert values[0].value_native == 2000
    assert values[0].value_base == 2700
    assert values[0].fx_rate_to_base == 1.35
    assert values[0].mark_price == 200
    assert values[0].cost_basis_price == 150
    assert values[0].fifo_pnl_unrealized == 350
    assert sum(value.value_base for value in values) == 3340


def test_parse_flex_cash_reports_extracts_native_cash_report():
    xml_text = Path("tests/fixtures/sample_flex.xml").read_text()

    cash_reports = parse_flex_cash_reports(xml_text)

    assert [report.currency for report in cash_reports] == ["CAD", "USD"]
    assert cash_reports[0].account_label == "U111111"
    assert cash_reports[0].ending_cash == 28500
    assert cash_reports[0].deposits == 28500
    assert cash_reports[0].from_date == "2026-01-01"
    assert cash_reports[1].ending_cash == -9657.98


def test_flex_date_parses_timestamped_and_date_only_values():
    assert _date("20260611;202000") == "2026-06-11"
    assert _date("20260323") == "2026-03-23"
    assert _date("2026-06-11") == "2026-06-11"


def test_cash_type_maps_observed_ibkr_cash_vocabulary():
    assert _cash_type("Broker Interest Paid", -2.50) == "FEE"
    assert _cash_type("Withholding Tax", -1.50) == "FEE"
    assert _cash_type("Payment In Lieu Of Dividends", 5.0) == "DIVIDEND"
    assert _cash_type("Deposits/Withdrawals", 10.0) == "DEPOSIT"
    assert _cash_type("Deposits/Withdrawals", -10.0) == "WITHDRAWAL"


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


def test_flex_client_does_not_retry_send_request_errors(monkeypatch):
    calls = []

    def fake_open_url(url):
        calls.append(url)
        return """
        <FlexStatementResponse>
          <Status>Fail</Status>
          <ErrorCode>1020</ErrorCode>
          <ErrorMessage>Invalid request or unable to validate request.</ErrorMessage>
        </FlexStatementResponse>
        """

    monkeypatch.setattr(flex_module, "_open_url", fake_open_url)
    monkeypatch.setattr(flex_module.time, "sleep", lambda seconds: None)

    client = FlexClient("https://example.test")

    with pytest.raises(RuntimeError) as exc:
        client.fetch_statement("token", "query")

    assert len(calls) == 1
    assert "ErrorCode=1020" in str(exc.value)


def test_flex_client_uses_prior_polling_window(monkeypatch):
    responses = iter(
        [
            """
            <FlexStatementResponse>
              <Status>Success</Status>
              <ReferenceCode>ABC123</ReferenceCode>
            </FlexStatementResponse>
            """,
            "<FlexStatementResponse><Status>Success</Status><Message>Statement generation in progress</Message></FlexStatementResponse>",
            "<FlexStatementResponse><Status>Success</Status><Message>Statement generation in progress</Message></FlexStatementResponse>",
        ]
    )
    sleeps = []

    monkeypatch.setattr(flex_module, "_open_url", lambda url: next(responses))
    monkeypatch.setattr(flex_module.time, "sleep", lambda seconds: sleeps.append(seconds))

    client = FlexClient("https://example.test")

    with pytest.raises(RuntimeError) as exc:
        client.fetch_statement("token", "query", max_attempts=2)

    assert sleeps == [3, 3]
    assert "not ready after polling" in str(exc.value)
