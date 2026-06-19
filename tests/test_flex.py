from pathlib import Path

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
