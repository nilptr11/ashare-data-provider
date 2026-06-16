import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_data_provider.local_store import (
    LocalDataEmptyError,
    LocalDataMissError,
    LocalDataStore,
    LocalDataStoreError,
    default_data_dir,
    normalize_cache_params,
)


class LocalDataStoreTest(unittest.TestCase):
    def test_normalize_params_drops_none_and_formats_dates(self) -> None:
        self.assertEqual(
            normalize_cache_params({"idx_type": "概念板块", "trade_date": "2026-06-12", "empty": None}),
            {"idx_type": "概念板块", "trade_date": "20260612"},
        )

    def test_write_and_read_successful_dataframe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            frame = pd.DataFrame([{"trade_date": "20260612", "ts_code": "BK001.DC", "close": 1.23}])

            store.write("dc_daily", {"trade_date": "20260612", "idx_type": "概念板块"}, "ts_code,trade_date,close", frame)
            read_back = store.read("dc_daily", {"idx_type": "概念板块", "trade_date": "2026-06-12"}, "ts_code, trade_date, close")

            pd.testing.assert_frame_equal(read_back, frame)
            files = list(Path(tmp_dir).glob("tushare/dc_daily/**/snapshots/*.parquet"))
            self.assertEqual(len(files), 1)
            self.assertEqual(len(list(Path(tmp_dir).glob("tushare/dc_daily/**/current.json"))), 1)

    def test_write_and_read_successful_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            records = [{"trade_date": "20260612", "ts_code": "BK001.DC", "close": 1.23}]

            written = store.write("dc_daily", {"trade_date": "20260612"}, "ts_code,trade_date,close", records)
            read_back = store.read("dc_daily", {"trade_date": "20260612"}, "ts_code, trade_date, close")
            entry = store.entry("dc_daily", {"trade_date": "20260612"}, "ts_code, trade_date, close")

            self.assertEqual(written, records)
            self.assertEqual(read_back, records)
            self.assertEqual(entry.meta["value_type"], "records")

    def test_api_name_is_path_encoded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            frame = pd.DataFrame([{"trade_date": "20260612", "close": 1.23}])

            store.write("../daily/evil", {"trade_date": "20260612"}, None, frame)

            self.assertFalse((Path(tmp_dir) / "daily").exists())
            self.assertEqual(len(list((Path(tmp_dir) / "tushare").glob("*evil*/**/snapshots/*.parquet"))), 1)

    def test_api_name_dot_segments_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)

            with self.assertRaises(LocalDataStoreError):
                store.request_dir(".")
            with self.assertRaises(LocalDataStoreError):
                store.request_dir("..")
            with self.assertRaises(LocalDataStoreError):
                store.request_dir("")

    def test_path_values_include_type_to_avoid_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)

            self.assertNotEqual(
                store.request_dir("daily", {"trade_date": "20260612", "limit": 10}),
                store.request_dir("daily", {"trade_date": "20260612", "limit": "10"}),
            )
            self.assertNotEqual(
                store.request_dir("daily", {"trade_date": "20260612", "flag": True}),
                store.request_dir("daily", {"trade_date": "20260612", "flag": "True"}),
            )

    def test_empty_string_path_value_does_not_collide_with_literal_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            empty_dir = store.request_dir("daily", {"trade_date": "20260612", "flag": ""})
            marker_dir = store.request_dir("daily", {"trade_date": "20260612", "flag": "__empty__"})

            store.write("daily", {"trade_date": "20260612", "flag": ""}, None, pd.DataFrame([{"close": 1.0}]))
            store.write("daily", {"trade_date": "20260612", "flag": "__empty__"}, None, pd.DataFrame([{"close": 2.0}]))

            self.assertNotEqual(empty_dir, marker_dir)
            self.assertEqual(float(store.read("daily", {"trade_date": "20260612", "flag": ""}).iloc[0]["close"]), 1.0)
            self.assertEqual(float(store.read("daily", {"trade_date": "20260612", "flag": "__empty__"}).iloc[0]["close"]), 2.0)

    def test_source_name_cannot_escape_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir) / "base"
            store = LocalDataStore(data_dir=base, source="../escaped")
            frame = pd.DataFrame([{"trade_date": "20260612", "close": 1.23}])

            store.write("daily", {"trade_date": "20260612"}, None, frame)
            entry = store.entry("daily", {"trade_date": "20260612"})

            self.assertFalse((Path(tmp_dir) / "escaped").exists())
            self.assertIn(base.resolve(), entry.data_path.resolve().parents)

    def test_invalid_source_name_is_rejected(self) -> None:
        with self.assertRaises(LocalDataStoreError):
            LocalDataStore(source="..")

    def test_missing_success_meta_is_not_a_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)

            with self.assertRaises(LocalDataMissError):
                store.read("daily", {"trade_date": "20260612"})

    def test_corrupt_parquet_is_treated_as_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            frame = pd.DataFrame([{"trade_date": "20260612", "close": 1.23}])
            store.write("daily", {"trade_date": "20260612"}, None, frame)
            entry = store.entry("daily", {"trade_date": "20260612"})
            entry.data_path.write_text("not parquet", encoding="utf-8")

            with self.assertRaises(LocalDataMissError):
                store.read("daily", {"trade_date": "20260612"})

    def test_write_refuses_empty_dataframe_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)

            with self.assertRaises(LocalDataEmptyError):
                store.write("daily", {"trade_date": "20260612"}, None, pd.DataFrame(columns=["trade_date", "close"]))

            self.assertFalse(store.entry("daily", {"trade_date": "20260612"}).meta_path.exists())

    def test_write_refuses_ragged_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)

            with self.assertRaises(LocalDataStoreError):
                store.write("daily", {"trade_date": "20260612"}, None, [{"trade_date": "20260612"}, {"close": 1.23}])

            self.assertFalse(store.entry("daily", {"trade_date": "20260612"}).meta_path.exists())

    def test_unpublished_snapshot_file_is_not_a_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            directory = store.request_dir("daily", {"trade_date": "20260612"})
            snapshots_dir = directory / "snapshots"
            snapshots_dir.mkdir(parents=True)
            pd.DataFrame([{"trade_date": "20260612", "close": 1.23}]).to_parquet(snapshots_dir / "orphan.parquet", index=False)

            with self.assertRaises(LocalDataMissError):
                store.read("daily", {"trade_date": "20260612"})

    def test_current_json_with_empty_rows_is_not_a_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            store.write("daily", {"trade_date": "20260612"}, None, pd.DataFrame([{"trade_date": "20260612", "close": 1.23}]))
            entry = store.entry("daily", {"trade_date": "20260612"})
            meta = dict(entry.meta)
            meta["rows"] = 0
            entry.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(LocalDataMissError):
                store.read("daily", {"trade_date": "20260612"})

    def test_current_json_columns_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            store.write("daily", {"trade_date": "20260612"}, None, pd.DataFrame([{"trade_date": "20260612", "close": 1.23}]))
            entry = store.entry("daily", {"trade_date": "20260612"})
            meta = dict(entry.meta)
            meta.pop("columns")
            entry.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(LocalDataMissError):
                store.read("daily", {"trade_date": "20260612"})

    def test_current_json_value_type_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            store.write("daily", {"trade_date": "20260612"}, None, pd.DataFrame([{"trade_date": "20260612", "close": 1.23}]))
            entry = store.entry("daily", {"trade_date": "20260612"})
            meta = dict(entry.meta)
            meta.pop("value_type")
            entry.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(LocalDataMissError):
                store.read("daily", {"trade_date": "20260612"})

    def test_current_json_data_file_must_match_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            store.write("daily", {"trade_date": "20260612"}, None, pd.DataFrame([{"trade_date": "20260612", "close": 1.23}]))
            entry = store.entry("daily", {"trade_date": "20260612"})
            meta = dict(entry.meta)
            meta["data_file"] = "snapshots/other.parquet"
            entry.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(LocalDataMissError):
                store.read("daily", {"trade_date": "20260612"})

    def test_current_json_source_must_match_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            store.write("daily", {"trade_date": "20260612"}, None, pd.DataFrame([{"trade_date": "20260612", "close": 1.23}]))
            entry = store.entry("daily", {"trade_date": "20260612"})
            meta = dict(entry.meta)
            meta["source"] = "other"
            entry.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(LocalDataMissError):
                store.read("daily", {"trade_date": "20260612"})

    def test_current_json_snapshot_id_must_be_uuid_hex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            store.write("daily", {"trade_date": "20260612"}, None, pd.DataFrame([{"trade_date": "20260612", "close": 1.23}]))
            entry = store.entry("daily", {"trade_date": "20260612"})
            meta = dict(entry.meta)
            meta["snapshot_id"] = "not-a-snapshot"
            meta["data_file"] = "snapshots/not-a-snapshot.parquet"
            entry.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(LocalDataMissError):
                store.read("daily", {"trade_date": "20260612"})

    def test_default_data_dir_reads_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_dir = Path(tmp_dir) / "config"
            config_dir.mkdir()
            env_file = config_dir / ".env"
            env_file.write_text("ASHARE_DATA_DIR=local-data\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(default_data_dir(env_file), config_dir / "local-data")


if __name__ == "__main__":
    unittest.main()
