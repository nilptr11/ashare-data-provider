import json

from ashare_research.cli import main
from ashare_research.protocols import ProtocolRegistry


def test_builtin_protocol_registry_loads_single_research_protocol():
    registry = ProtocolRegistry.builtin()
    specs = registry.list()

    assert [spec.protocol_id for spec in specs] == ["industry_chain_selection.v1"]
    spec = specs[0]
    assert spec.title == "主线选股与产业链拆解"
    assert "market_data" in spec.required_inputs
    assert "candidate_pool" in spec.required_sections
    assert any("position sizing" in item for item in spec.forbidden)


def test_protocol_registry_validate():
    payload = ProtocolRegistry.builtin().validate("industry_chain_selection.v1")

    assert payload["status"] == "ready"
    assert payload["protocols"][0]["protocol_id"] == "industry_chain_selection.v1"
    assert payload["protocols"][0]["output_schema_status"] == "ready"


def test_protocol_registry_validate_all_protocols():
    payload = ProtocolRegistry.builtin().validate()
    protocol_ids = {item["protocol_id"] for item in payload["protocols"]}

    assert payload["status"] == "ready"
    assert protocol_ids == {"industry_chain_selection.v1"}
    assert all(item["output_schema_status"] == "ready" for item in payload["protocols"])


def test_protocol_registry_loads_industry_chain_output_schema():
    registry = ProtocolRegistry.builtin()
    schema = registry.output_schema("ashare.protocol_output.industry_chain_selection.v1")

    assert schema["$id"] == "ashare.protocol_output.industry_chain_selection.v1"
    assert "research_scope" in schema["properties"]
    assert "candidate_pool" in schema["properties"]


def test_cli_protocols_list_show_validate(capsys):
    exit_code = main(["protocols", "list", "--format", "json"])
    assert exit_code == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert [item["protocol_id"] for item in list_payload] == ["industry_chain_selection.v1"]

    exit_code = main(["protocols", "show", "industry_chain_selection.v1"])
    assert exit_code == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["output_schema"] == "ashare.protocol_output.industry_chain_selection.v1"

    exit_code = main(["protocols", "validate", "industry_chain_selection.v1"])
    assert exit_code == 0
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["status"] == "ready"

    exit_code = main(["protocols", "output-schema", "industry_chain_selection.v1"])
    assert exit_code == 0
    schema_payload = json.loads(capsys.readouterr().out)
    assert schema_payload["$id"] == "ashare.protocol_output.industry_chain_selection.v1"
