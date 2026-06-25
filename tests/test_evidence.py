import json

import pytest
import pandas as pd

from ashare_research.cli import main
from ashare_research.connectors import ConnectorRegistry, ConnectorSpec
from ashare_research.evidence import EvidenceStore
from ashare_research.evidence.schemas import EvidenceError
from ashare_research.evidence.sources import EvidenceSource, EvidenceSourceFetcher, EvidenceSourceRegistry
from ashare_research.schemas import SourceResponse


def _official_record(**overrides):
    payload = {
        "claim": "Microsoft FY2026 Q1 capital expenditures were 34.9 billion USD.",
        "topic": "capex",
        "industry": "ai_infrastructure",
        "product": "data_center",
        "company": "Microsoft",
        "region": "United States",
        "metric": "capital_expenditures",
        "value": 34.9,
        "unit": "USD billion",
        "period": "FY2026 Q1",
        "frequency": "quarterly",
        "source_type": "company_ir",
        "source_name": "Microsoft Investor Relations",
        "source_url": "https://example.com/msft-ir",
        "published_at": "2025-10-29",
        "query_time": "2026-06-24T15:26:50+08:00",
        "confidence": "high",
        "verification": "official_single_source",
        "needs_source": True,
        "raw_excerpt": "short excerpt",
        "supports": ["ai_infra_capex_trend"],
    }
    payload.update(overrides)
    return payload


def test_evidence_store_ingests_dedupes_and_searches(tmp_path):
    store = EvidenceStore(tmp_path)
    result = store.ingest_evidence([_official_record(), _official_record()])

    assert result.inserted == 1
    assert result.skipped_duplicates == 1

    records = store.find_evidence(topic="capex", industry="ai_infrastructure", company="Microsoft")
    assert len(records) == 1
    assert records[0].confidence_score == pytest.approx(0.95)
    assert records[0].evidence_id


def test_evidence_validation_requires_complete_numerical_fields(tmp_path):
    store = EvidenceStore(tmp_path)
    payload = _official_record(unit=None)

    with pytest.raises(EvidenceError, match="Numerical evidence missing fields"):
        store.validate_evidence(payload)


def test_unknown_source_type_is_rejected(tmp_path):
    store = EvidenceStore(tmp_path)
    payload = _official_record(
        source_type="unsupported_source",
        source_name="Unsupported Source",
        source_url="https://example.com/source",
        confidence="high",
        verification="single_source",
    )

    with pytest.raises(EvidenceError, match="Invalid source_type"):
        store.validate_evidence(payload)


def test_evidence_source_candidates_for_repeated_numerical_series(tmp_path):
    store = EvidenceStore(tmp_path)
    store.ingest_evidence(
        [
            _official_record(period="FY2026 Q1", value=34.9, source_url="https://example.com/msft-ir-q1"),
            _official_record(period="FY2026 Q2", value=35.8, source_url="https://example.com/msft-ir-q2"),
        ]
    )

    candidates = store.source_candidates(min_records=2)

    assert len(candidates) == 1
    assert candidates[0]["metric"] == "capital_expenditures"
    assert candidates[0]["records"] == 2


def test_evidence_maturity_accepts_fetched_records(tmp_path):
    store = EvidenceStore(tmp_path)
    result = store.ingest_evidence(_official_record(maturity="fetched", source_id="source:capex"))

    record = store.read_records()[0]
    assert result.inserted == 1
    assert record.maturity == "fetched"
    assert record.source_id == "source:capex"


def test_cli_evidence_source_candidates_and_sources_list(capsys, tmp_path):
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(
        json.dumps(
            [
                _official_record(period="FY2026 Q1", value=34.9, source_url="https://example.com/msft-ir-q1"),
                _official_record(period="FY2026 Q2", value=35.8, source_url="https://example.com/msft-ir-q2"),
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert main(["--data-dir", str(tmp_path), "evidence", "ingest", str(evidence_file)]) == 0
    capsys.readouterr()

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "evidence",
            "source-candidates",
            "--min-records",
            "2",
            "--format",
            "json",
        ]
    )
    assert exit_code == 0
    candidates = json.loads(capsys.readouterr().out)
    assert len(candidates) == 1
    assert candidates[0]["metric"] == "capital_expenditures"

    source_file = tmp_path / "source.json"
    source_file.write_text(json.dumps(_source_payload(), ensure_ascii=False), encoding="utf-8")
    exit_code = main(["--data-dir", str(tmp_path), "evidence", "sources", "add", str(source_file)])
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["source_id"] == "source:capex"
    exit_code = main(["--data-dir", str(tmp_path), "evidence", "sources", "list", "--format", "json"])
    assert exit_code == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload[0]["metric"] == "capital_expenditures"
    assert "status" not in list_payload[0]


def test_evidence_source_fetcher_ingests_fetched_records(tmp_path):
    source = _source_payload()
    registry = EvidenceSourceRegistry(tmp_path)
    registry.add(EvidenceSource.from_dict(source))
    fetcher = EvidenceSourceFetcher(
        evidence_store=EvidenceStore(tmp_path),
        source_registry=registry,
        connector_registry=_fake_connector_registry(),
    )

    result = fetcher.fetch("source:capex")

    assert result.inserted == 1
    record = EvidenceStore(tmp_path).read_records()[0]
    assert record.maturity == "fetched"
    assert record.source_id == "source:capex"
    assert record.value == 34.9


def test_cli_evidence_sources_add_and_fetch(monkeypatch, capsys, tmp_path):
    source_file = tmp_path / "source.json"
    source_file.write_text(json.dumps(_source_payload(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ConnectorRegistry, "builtin", classmethod(lambda cls: _fake_connector_registry()))

    exit_code = main(["--data-dir", str(tmp_path), "evidence", "sources", "add", str(source_file)])
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["source_id"] == "source:capex"

    exit_code = main(["--data-dir", str(tmp_path), "evidence", "sources", "fetch", "source:capex"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inserted"] == 1


def test_cli_evidence_ingest_search_export_and_collect(capsys, tmp_path):
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(json.dumps([_official_record()], ensure_ascii=False), encoding="utf-8")

    exit_code = main(["--data-dir", str(tmp_path), "evidence", "ingest", str(evidence_file)])
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["inserted"] == 1

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "evidence",
            "search",
            "--topic",
            "capex",
            "--company",
            "Microsoft",
            "--format",
            "json",
        ]
    )
    assert exit_code == 0
    search_payload = json.loads(capsys.readouterr().out)
    assert search_payload[0]["company"] == "Microsoft"

    output_path = tmp_path / "export" / "records.jsonl"
    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "evidence",
            "export",
            str(output_path),
            "--industry",
            "ai_infrastructure",
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["records"] == 1
    assert output_path.exists()

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "evidence",
            "collect",
            "--question",
            "AI 算力硬件链是否被基本面验证",
            "--as-of",
            "20260623",
        ]
    )
    assert exit_code == 0
    collect_payload = json.loads(capsys.readouterr().out)
    assert collect_payload["status"] == "needs_external_sources"


def _source_payload():
    return {
        "source_id": "source:capex",
        "source_type": "company_ir",
        "source_name": "Microsoft Investor Relations",
        "topic": "capex",
        "industry": "ai_infrastructure",
        "metric": "capital_expenditures",
        "frequency": "quarterly",
        "connector": "fake",
        "api_name": "capex_api",
        "params_template": {},
        "field_mapping": {
            "claim": "claim",
            "source_url": "source_url",
            "published_at": "published_at",
            "query_time": "query_time",
            "value": "value",
            "unit": "unit",
            "period": "period",
            "confidence": "confidence",
            "verification": "verification",
        },
    }


def _fake_connector_registry():
    class FakeConnector:
        source = "fake"

        def fetch(self, api_name, params, fields=None):
            frame = pd.DataFrame(
                [
                    {
                        "claim": "Microsoft capex was 34.9 billion USD.",
                        "source_url": "https://example.com/msft-ir-q1",
                        "published_at": "2025-10-29",
                        "query_time": "2026-06-24T15:26:50+08:00",
                        "value": 34.9,
                        "unit": "USD billion",
                        "period": "FY2026 Q1",
                        "confidence": "high",
                        "verification": "source_mapped",
                    }
                ]
            )
            return SourceResponse(
                source=self.source,
                api_name=api_name,
                params=dict(params),
                fields=tuple(fields or ()),
                rows=len(frame),
                columns=tuple(frame.columns),
                requested_at="2026-06-24T18:00:00+08:00",
                frame=frame,
            )

    return ConnectorRegistry([ConnectorSpec(name="fake", title="Fake", factory=lambda **_: FakeConnector(), kind="test")])
