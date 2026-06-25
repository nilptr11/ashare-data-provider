import json
from pathlib import Path

import pandas as pd

from ashare_research.cli import main
from ashare_research.features import FeatureRegistry, FeatureStore
from ashare_research.marts.publisher import MartPublisher
from ashare_research.runs import RunRecorder, replay_run


def test_run_recorder_records_and_replays(tmp_path):
    _write_run_data_refs(tmp_path)
    recorder = RunRecorder(tmp_path, runs_dir=tmp_path / "runs")

    manifest = recorder.record(
        question="分析 AI 算力硬件链",
        as_of="20260623",
        mart_refs=["daily:trade_date=20260623"],
        feature_refs=["market_strength:as_of=20260623,window=20"],
        run_id="test_run",
    )

    run_dir = Path(manifest["path"])
    assert (run_dir / "run.json").exists()
    assert (run_dir / "data_refs.json").exists()
    assert (run_dir / "agent_reasoning.json").exists()
    assert "data_refs: `data_refs.json`" in (run_dir / "report.md").read_text(encoding="utf-8")
    assert "not a factual source" in (run_dir / "report.md").read_text(encoding="utf-8")
    data_refs = json.loads((run_dir / "data_refs.json").read_text(encoding="utf-8"))
    assert data_refs["marts"][0]["partition"] == {"trade_date": "20260623"}
    assert data_refs["features"][0]["partition"] == {"as_of": "20260623", "window": "20"}
    assert data_refs["validation"]["status"] == "ready"
    assert data_refs["marts"][0]["status"] == "ready"
    assert data_refs["features"][0]["status"] == "ready"
    assert manifest["agent_reasoning"]["status"] == "not_provided"
    assert manifest["quality_gates"]["status"] == "warning"
    assert manifest["quality_gates"]["gates"]["data_refs_gate"]["status"] == "passed"

    replay = replay_run(run_dir)
    assert replay["status"] == "replayable"
    assert replay["quality_status"] == "warning"
    assert any(item["kind"] == "data_refs" for item in replay["artifacts"])


def test_run_recorder_passes_supported_validated_output(tmp_path):
    _write_run_data_refs(tmp_path)
    recorder = RunRecorder(tmp_path, runs_dir=tmp_path / "runs")

    manifest = recorder.record(
        question="分析 AI 算力硬件链",
        as_of="20260623",
        mart_refs=["daily:trade_date=20260623"],
        feature_refs=["market_strength:as_of=20260623,window=20"],
        validated_output=_research_output(),
        run_id="supported_output_run",
    )

    assert manifest["quality_gates"]["status"] == "passed"
    assert manifest["quality_gates"]["gates"]["output_gate"]["status"] == "passed"
    assert manifest["quality_gates"]["gates"]["source_gate"]["status"] == "passed"
    assert manifest["quality_gates"]["gates"]["source_audit_gate"]["status"] == "passed"
    assert manifest["quality_gates"]["gates"]["confidence_gate"]["status"] == "passed"


def test_run_recorder_blocks_core_candidate_without_exposure_source(tmp_path):
    _write_run_data_refs(tmp_path)
    recorder = RunRecorder(tmp_path, runs_dir=tmp_path / "runs")

    manifest = recorder.record(
        question="分析 AI 算力硬件链",
        as_of="20260623",
        mart_refs=["daily:trade_date=20260623"],
        feature_refs=["market_strength:as_of=20260623,window=20"],
        validated_output=_research_output(exposure_source_kind="feature"),
        run_id="feature_only_exposure_run",
    )

    assert manifest["quality_gates"]["status"] == "blocked"
    source_gate = manifest["quality_gates"]["gates"]["source_gate"]
    assert source_gate["status"] == "blocked"
    assert source_gate["details"]["items"][0]["source_kinds"] == ["feature"]


def test_run_recorder_blocks_core_candidate_with_weak_evidence(tmp_path):
    _write_run_data_refs(tmp_path)
    recorder = RunRecorder(tmp_path, runs_dir=tmp_path / "runs")

    manifest = recorder.record(
        question="分析 AI 算力硬件链",
        as_of="20260623",
        mart_refs=["daily:trade_date=20260623"],
        feature_refs=["market_strength:as_of=20260623,window=20"],
        validated_output=_research_output(evidence_strength="weak"),
        run_id="weak_core_evidence_run",
    )

    assert manifest["quality_gates"]["status"] == "blocked"
    confidence_gate = manifest["quality_gates"]["gates"]["confidence_gate"]
    assert confidence_gate["status"] == "blocked"
    assert confidence_gate["details"]["candidates"] == ["000001.SZ"]


def test_run_recorder_blocks_external_evidence_without_audit_fields(tmp_path):
    _write_run_data_refs(tmp_path)
    recorder = RunRecorder(tmp_path, runs_dir=tmp_path / "runs")

    manifest = recorder.record(
        question="分析 AI 算力硬件链",
        as_of="20260623",
        mart_refs=["daily:trade_date=20260623"],
        feature_refs=["market_strength:as_of=20260623,window=20"],
        validated_output=_research_output(auditable=False),
        run_id="missing_source_audit_run",
    )

    assert manifest["quality_gates"]["status"] == "blocked"
    source_audit_gate = manifest["quality_gates"]["gates"]["source_audit_gate"]
    assert source_audit_gate["status"] == "blocked"
    assert source_audit_gate["details"]["items"][0]["missing_fields"] == [
        "source_name",
        "source_url",
        "published_at",
        "query_time",
    ]


def test_run_recorder_blocks_relation_exposure_without_source_id(tmp_path):
    _write_run_data_refs(tmp_path)
    recorder = RunRecorder(tmp_path, runs_dir=tmp_path / "runs")

    manifest = recorder.record(
        question="分析 AI 算力硬件链",
        as_of="20260623",
        mart_refs=["daily:trade_date=20260623"],
        feature_refs=["market_strength:as_of=20260623,window=20"],
        validated_output=_research_output(exposure_source_kind="relations", source_id=False),
        run_id="relation_without_source_id_run",
    )

    assert manifest["quality_gates"]["status"] == "blocked"
    source_gate = manifest["quality_gates"]["gates"]["source_gate"]
    assert source_gate["status"] == "blocked"
    assert source_gate["details"]["items"][0]["source_kinds"] == ["relations"]


def test_run_recorder_blocks_missing_data_refs(tmp_path):
    recorder = RunRecorder(tmp_path, runs_dir=tmp_path / "runs")

    manifest = recorder.record(
        question="分析 AI 算力硬件链",
        as_of="20260623",
        mart_refs=["daily:trade_date=20260623"],
        feature_refs=["market_strength:as_of=20260623,window=20"],
        run_id="missing_refs_run",
    )

    run_dir = Path(manifest["path"])
    data_refs = json.loads((run_dir / "data_refs.json").read_text(encoding="utf-8"))

    assert data_refs["validation"]["status"] == "blocked"
    assert data_refs["marts"][0]["status"] == "missing"
    assert data_refs["features"][0]["status"] == "missing"
    assert manifest["quality_gates"]["status"] == "blocked"
    assert manifest["quality_gates"]["gates"]["data_refs_gate"]["status"] == "blocked"


def test_cli_runs_record_list_replay(capsys, tmp_path):
    _write_run_data_refs(tmp_path)
    runs_dir = tmp_path / "runs"
    reasoning_path = tmp_path / "agent_reasoning.json"
    reasoning_path.write_text(
        json.dumps(
            {
                "schema": "ashare.agent_reasoning.v1",
                "status": "provided",
                "facts_used": [],
                "inferences": ["市场结构偏强"],
                "hypotheses": ["算力链可能有事件催化"],
                "unverified_claims": [],
                "validation_steps": [],
                "open_questions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "runs",
            "record",
            "--question",
            "分析 AI 算力硬件链",
            "--as-of",
            "20260623",
            "--mart-ref",
            "daily:trade_date=20260623",
            "--feature-ref",
            "market_strength:as_of=20260623,window=20",
            "--agent-reasoning",
            str(reasoning_path),
            "--runs-dir",
            str(runs_dir),
            "--run-id",
            "cli_run",
        ]
    )
    assert exit_code == 0
    record_payload = json.loads(capsys.readouterr().out)
    run_dir = record_payload["path"]
    assert record_payload["agent_reasoning"]["status"] == "provided"

    exit_code = main(["--data-dir", str(tmp_path), "runs", "list", "--runs-dir", str(runs_dir), "--format", "json"])
    assert exit_code == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload[0]["run_id"] == "cli_run"

    exit_code = main(["--data-dir", str(tmp_path), "runs", "replay", run_dir])
    assert exit_code == 0
    replay_payload = json.loads(capsys.readouterr().out)
    assert replay_payload["status"] == "replayable"


def _research_output(*, exposure_source_kind="evidence", evidence_strength="strong", auditable=True, source_id=True):
    exposure_fact = {
        "source_kind": exposure_source_kind,
        "claim": "公司披露了 AI 算力硬件相关订单或产品暴露",
    }
    if source_id:
        exposure_fact["source_id"] = "evidence:ai-infra-order" if exposure_source_kind != "relations" else "relation:company_product:000001.SZ:ai_infra"
    if auditable:
        exposure_fact |= {
            "source_type": "company_filing",
            "source_name": "测试公司 2025 年年度报告",
            "source_url": "https://example.com/annual-report",
            "published_at": "2026-04-15",
            "query_time": "2026-06-23T20:00:00+08:00",
        }
    market_fact = {
        "source_kind": "mart",
        "source_id": "daily:trade_date=20260623",
        "claim": "当日行情分区可用",
    }
    return {
        "schema": "ashare.research_output.v1",
        "as_of": "20260623",
        "question": "分析 AI 算力硬件链",
        "theme_identification": {
            "summary": "AI 算力硬件链存在市场关注线索",
            "facts": [market_fact],
            "inference": "需要继续验证公司暴露度",
            "confidence": "medium",
        },
        "industry_chain_map": [
            {
                "segment_id": "optical",
                "segment_name": "光模块",
                "chain_layer": "components",
                "role": "算力网络传输组件",
                "prosperity_drivers": ["AI capex"],
                "constraints": ["产能和客户验证"],
                "facts": [exposure_fact],
                "confidence": "medium",
            }
        ],
        "revaluation_segments": [
            {
                "segment_id": "optical",
                "reason": "市场关注和产业证据共同支持继续研究",
                "market_validation": {
                    "summary": "行情可用",
                    "facts": [market_fact],
                    "inference": "有市场关注线索",
                    "confidence": "medium",
                },
                "fundamental_validation": {
                    "summary": "存在公司暴露证据",
                    "facts": [exposure_fact],
                    "inference": "可进入核心研究",
                    "confidence": "medium",
                },
                "risk_flags": [],
                "confidence": "medium",
            }
        ],
        "company_mapping": [
            {
                "ts_code": "000001.SZ",
                "name": "测试公司",
                "segments": ["optical"],
                "exposure_level": "direct",
                "exposure_evidence": [exposure_fact],
                "market_validation": {
                    "summary": "行情分区可用",
                    "facts": [market_fact],
                    "inference": "市场数据支持交叉验证",
                    "confidence": "medium",
                },
                "fundamental_validation": {
                    "summary": "暴露证据可追溯",
                    "facts": [exposure_fact],
                    "inference": "业务暴露度不是概念成分单独推断",
                    "confidence": "medium",
                },
                "risk_flags": [],
                "confidence": "medium",
            }
        ],
        "candidate_pool": [
            {
                "ts_code": "000001.SZ",
                "name": "测试公司",
                "priority": "high",
                "rationale": "公司暴露度和市场数据均有可追溯引用",
                "evidence_strength": evidence_strength,
                "missing_evidence": [],
                "risk_flags": [],
            }
        ],
        "evidence_matrix": [
            {
                "topic": "company_exposure",
                "claim": "公司披露了 AI 算力硬件相关订单或产品暴露",
                "source_kind": "evidence",
                "source_id": "evidence:ai-infra-order",
                **(
                    {
                        "source_type": "company_filing",
                        "source_name": "测试公司 2025 年年度报告",
                        "source_url": "https://example.com/annual-report",
                        "published_at": "2026-04-15",
                        "query_time": "2026-06-23T20:00:00+08:00",
                    }
                    if auditable
                    else {}
                ),
                "supports": ["000001.SZ", "optical"],
                "verification": "official_single_source",
                "confidence": "high",
            }
        ],
        "data_gaps": [],
        "follow_up_plan": [],
        "invalid_if": ["后续公告否认相关业务暴露"],
        "confidence": "medium",
    }


def _write_run_data_refs(data_dir):
    MartPublisher(data_dir).publish(
        "daily",
        pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260623",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "pct_chg": 5.0,
                    "vol": 100.0,
                    "amount": 1000.0,
                }
            ]
        ),
        partition={"trade_date": "20260623"},
        source={"kind": "fixture"},
    )
    spec = FeatureRegistry.builtin().require("market_strength")
    FeatureStore(data_dir).write_partition(
        spec,
        pd.DataFrame(
            [
                {
                    "as_of": "20260623",
                    "window": 20,
                    "ts_code": "000001.SH",
                    "strength_score": 1.0,
                }
            ]
        ),
        as_of="20260623",
        window=20,
        inputs=[
            {"dataset": "index_daily", "status": "ready", "rows": 1},
            {"dataset": "index_dailybasic", "status": "ready", "rows": 1},
        ],
    )
