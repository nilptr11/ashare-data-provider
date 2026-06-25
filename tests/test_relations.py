import json

import pytest

from ashare_research.cli import main
from ashare_research.relations import RelationStore
from ashare_research.relations.schemas import RelationError, RelationRecord


def _relations_record(**overrides):
    payload = {
        "id": "company_product:603938.SH:high_purity_silicon_tetrachloride",
        "subject": {
            "type": "company",
            "id": "603938.SH",
            "name": "三孚股份",
            "aliases": ["Sanfu"],
        },
        "predicate": "has_product_exposure",
        "object": {
            "type": "product",
            "id": "high_purity_silicon_tetrachloride",
            "name": "高纯四氯化硅",
            "aliases": ["HP SiCl4"],
        },
        "confidence": "medium",
        "source": {
            "source_type": "company_filing",
            "source_name": "annual report",
            "source_url": "https://example.com/sanfu-annual-report",
            "published_at": "2026-04-15",
            "query_time": "2026-06-24T20:00:00+08:00",
        },
        "valid_from": "2026-04-15",
        "updated_at": "2026-06-24T00:00:00+08:00",
        "tags": ["ai_infrastructure", "materials"],
    }
    payload.update(overrides)
    return payload


def test_relations_ingest_search_and_snapshot(tmp_path):
    store = RelationStore(tmp_path)

    ingest_result = store.ingest_records(_relations_record())

    assert ingest_result.inserted == 1
    assert ingest_result.record_ids == ("company_product:603938.SH:high_purity_silicon_tetrachloride",)
    records = store.search(entity="三孚", predicate="has_product_exposure")
    assert len(records) == 1
    assert records[0].object_ref.name == "高纯四氯化硅"

    snapshot = store.snapshot()
    assert snapshot["record_count"] == 1
    assert snapshot["records_sha256"]


def test_relations_requires_traceable_source(tmp_path):
    store = RelationStore(tmp_path)
    payload = _relations_record(source={"source_type": "company_filing"})

    with pytest.raises(RelationError, match="source requires source_url, evidence_id, or raw_ref"):
        store.ingest_records(payload)


def test_relations_accepts_codex_inference_with_raw_ref_and_note(tmp_path):
    store = RelationStore(tmp_path)
    payload = _relations_record(
        id="inferred_chain:ai_pcb:low_dk_glass_fiber",
        subject={"type": "product", "id": "low_dk_glass_fiber", "name": "低介电玻纤"},
        predicate="belongs_to",
        object={"type": "theme", "id": "ai_pcb_chain", "name": "AI PCB 产业链"},
        confidence="medium",
        source={
            "source_type": "codex_inference",
            "source_name": "Codex research run",
            "raw_ref": "runs/20260625_ai_pcb/run.json#model_output",
        },
        note="由 AI PCB 材料需求、玻纤介电性能要求和候选公司披露交叉推理得出。",
    )

    result = store.ingest_records(payload)

    assert result.record_ids == ("inferred_chain:ai_pcb:low_dk_glass_fiber",)


def test_relations_requires_note_for_codex_inference(tmp_path):
    store = RelationStore(tmp_path)
    payload = _relations_record(
        source={
            "source_type": "codex_inference",
            "source_name": "Codex research run",
            "raw_ref": "runs/20260625_ai_pcb/run.json#model_output",
        },
        note=None,
    )

    with pytest.raises(RelationError, match="note is required"):
        store.ingest_records(payload)


def test_relations_requires_raw_ref_for_codex_inference(tmp_path):
    store = RelationStore(tmp_path)
    payload = _relations_record(
        source={
            "source_type": "codex_inference",
            "source_name": "Codex research run",
        },
        note="由多条证据推理得出。",
    )

    with pytest.raises(RelationError, match="source.raw_ref is required"):
        store.ingest_records(payload)


def test_relations_requires_query_time_for_url_source(tmp_path):
    store = RelationStore(tmp_path)
    payload = _relations_record(
        source={
            "source_type": "company_filing",
            "source_name": "annual report",
            "source_url": "https://example.com/sanfu-annual-report",
            "published_at": "2026-04-15",
        }
    )

    with pytest.raises(RelationError, match="source.query_time is required"):
        store.ingest_records(payload)


def test_relations_requires_source_name_for_url_source(tmp_path):
    store = RelationStore(tmp_path)
    payload = _relations_record(
        source={
            "source_type": "company_filing",
            "source_url": "https://example.com/sanfu-annual-report",
            "published_at": "2026-04-15",
            "query_time": "2026-06-24T20:00:00+08:00",
        }
    )

    with pytest.raises(RelationError, match="source.source_name is required"):
        store.ingest_records(payload)


def test_relations_rejects_unknown_entity_type(tmp_path):
    store = RelationStore(tmp_path)
    payload = _relations_record(subject={"type": "issuer", "id": "603938.SH", "name": "三孚股份"})

    with pytest.raises(RelationError, match="unknown entity type"):
        store.ingest_records(payload)


def test_relations_rejects_unknown_predicate(tmp_path):
    store = RelationStore(tmp_path)
    payload = _relations_record(predicate="mentions")

    with pytest.raises(RelationError, match="unknown predicate"):
        store.ingest_records(payload)


def test_relations_rejects_invalid_relation_direction(tmp_path):
    store = RelationStore(tmp_path)
    payload = _relations_record(
        subject={"type": "product", "id": "high_purity_silicon_tetrachloride", "name": "高纯四氯化硅"},
        predicate="has_product_exposure",
        object={"type": "company", "id": "603938.SH", "name": "三孚股份"},
    )

    with pytest.raises(RelationError, match="not allowed for predicate"):
        store.ingest_records(payload)


def test_cli_relations_flow(capsys, tmp_path):
    relations_file = tmp_path / "relations.json"
    relations_file.write_text(json.dumps(_relations_record(), ensure_ascii=False), encoding="utf-8")

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "relations",
            "ingest",
            str(relations_file),
        ]
    )
    assert exit_code == 0
    ingest_payload = json.loads(capsys.readouterr().out)
    assert ingest_payload["record_ids"] == ["company_product:603938.SH:high_purity_silicon_tetrachloride"]

    exit_code = main(["--data-dir", str(tmp_path), "relations", "list", "--format", "json"])
    assert exit_code == 0
    assert len(json.loads(capsys.readouterr().out)) == 1

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "relations",
            "search",
            "--entity",
            "HP SiCl4",
            "--format",
            "json",
        ]
    )
    assert exit_code == 0
    search_payload = json.loads(capsys.readouterr().out)
    assert search_payload[0]["subject"]["id"] == "603938.SH"

    snapshot_path = tmp_path / "relations-snapshot.json"
    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "relations",
            "snapshot",
            "--output",
            str(snapshot_path),
        ]
    )
    assert exit_code == 0
    snapshot_payload = json.loads(capsys.readouterr().out)
    assert snapshot_payload["path"] == str(snapshot_path)
    assert snapshot_path.exists()


def test_cli_relations_taxonomy(capsys, tmp_path):
    exit_code = main(["--data-dir", str(tmp_path), "relations", "taxonomy"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "ashare.relation_taxonomy.v1"
    assert "company" in payload["entity_types"]
    assert any(item["predicate"] == "has_product_exposure" for item in payload["predicates"])


def test_relations_record_round_trip():
    record = RelationRecord.from_dict(_relations_record())

    assert RelationRecord.from_dict(record.to_dict()).to_dict() == record.to_dict()
