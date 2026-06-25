import json

import pandas as pd
import pytest

from ashare_research.cli import main


def test_cli_data_list_uses_new_package(capsys, tmp_path):
    exit_code = main(["--data-dir", str(tmp_path), "data", "list", "--format", "json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(row["name"] == "daily" and row["registered"] for row in payload)


def test_cli_mart_read(capsys, tmp_path):
    partition_dir = tmp_path / "mart" / "stock_basic" / "snapshot_date=20260623"
    partition_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "market": "主板", "list_status": "L"},
            {"ts_code": "000002.SZ", "symbol": "000002", "name": "万科A", "market": "主板", "list_status": "L"},
        ]
    ).to_parquet(partition_dir / "part.parquet", index=False)
    (partition_dir / "_meta.json").write_text(
        json.dumps(
            {
                "schema": "ashare.mart_partition.v1",
                "dataset": "stock_basic",
                "partition": {"snapshot_date": "20260623"},
                "rows": 2,
                "columns": ["ts_code", "symbol", "name", "market", "list_status"],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "mart",
            "read",
            "stock_basic",
            "--snapshot-date",
            "20260623",
            "--format",
            "json",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)[0]["ts_code"] == "000001.SZ"

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "mart",
            "read",
            "stock_basic",
            "--snapshot-date",
            "20260623",
            "--columns",
            "ts_code,name",
            "--sort",
            "ts_code",
            "--format",
            "json",
        ]
    )

    assert exit_code == 0
    sorted_payload = json.loads(capsys.readouterr().out)
    assert list(sorted_payload[0]) == ["ts_code", "name"]
    assert [row["ts_code"] for row in sorted_payload] == ["000002.SZ", "000001.SZ"]


def test_cli_feature_build_and_read(capsys, tmp_path):
    _write_partition(
        tmp_path,
        "index_daily",
        "trade_date",
        "20260622",
        [{"ts_code": "000001.SH", "trade_date": "20260622", "close": 100.0, "pct_chg": 0.0, "amount": 1000.0}],
    )
    _write_partition(
        tmp_path,
        "index_daily",
        "trade_date",
        "20260623",
        [{"ts_code": "000001.SH", "trade_date": "20260623", "close": 110.0, "pct_chg": 10.0, "amount": 2000.0}],
    )
    _write_partition(
        tmp_path,
        "index_dailybasic",
        "trade_date",
        "20260623",
        [
            {
                "ts_code": "000001.SH",
                "trade_date": "20260623",
                "turnover_rate": 1.5,
                "pe_ttm": 12.0,
                "pb": 1.1,
                "total_mv": 10000.0,
                "float_mv": 8000.0,
            }
        ],
    )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "feature",
            "build",
            "market_strength",
            "--as-of",
            "20260623",
            "--windows",
            "2",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)[0]["rows"] == 1

    exit_code = main(["--data-dir", str(tmp_path), "feature", "list", "--format", "json"])

    assert exit_code == 0
    feature_list = json.loads(capsys.readouterr().out)
    market_strength = next(row for row in feature_list if row["name"] == "market_strength")
    assert market_strength["published"] is True
    assert market_strength["partition_count"] == 1
    assert market_strength["latest_as_of"] == "20260623"
    assert market_strength["latest_windows"] == [2]

    exit_code = main(["--data-dir", str(tmp_path), "feature", "list", "--all-partitions", "--format", "json"])

    assert exit_code == 0
    all_partitions = json.loads(capsys.readouterr().out)
    assert any(
        row["name"] == "market_strength" and row["published"] is True and row["as_of"] == "20260623" and row["window"] == 2
        for row in all_partitions
    )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "feature",
            "read",
            "market_strength",
            "--as-of",
            "20260623",
            "--window",
            "2",
            "--format",
            "json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["window_return_pct"] == pytest.approx(10.0)
    assert payload[0]["latest_pe_ttm"] == 12.0

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "feature",
            "read",
            "market_strength",
            "--as-of",
            "20260623",
            "--window",
            "2",
            "--columns",
            "ts_code,strength_score",
            "--sort",
            "strength_score",
            "--limit",
            "1",
            "--format",
            "json",
        ]
    )

    assert exit_code == 0
    sorted_feature = json.loads(capsys.readouterr().out)
    assert list(sorted_feature[0]) == ["ts_code", "strength_score"]


def _write_partition(data_dir, dataset, key, value, rows):
    partition_dir = data_dir / "mart" / dataset / f"{key}={value}"
    partition_dir.mkdir(parents=True)
    frame = pd.DataFrame(rows)
    frame.to_parquet(partition_dir / "part.parquet", index=False)
    (partition_dir / "_meta.json").write_text(
        json.dumps(
            {
                "schema": "ashare.mart_partition.v1",
                "dataset": dataset,
                "partition": {key: value},
                "rows": len(frame),
                "columns": list(frame.columns),
            }
        ),
        encoding="utf-8",
    )
