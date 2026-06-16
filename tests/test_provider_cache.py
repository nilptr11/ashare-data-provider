import json
import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_data_provider.client import TushareCallError
from ashare_data_provider.local_store import LocalDataFormatError, LocalDataMissError, LocalDataStore
from ashare_data_provider.provider import AShareProvider
from ashare_data_provider.registry import InterfaceRegistry


class CallbackCaller:
    def __init__(self, callback) -> None:  # noqa: ANN001
        self.callback = callback
        self.calls = []

    def call(self, api_name, params=None, fields=None):  # noqa: ANN001
        self.calls.append({"api_name": api_name, "params": params, "fields": fields})
        return self.callback(api_name, params or {}, fields)


def make_registry(*api_names):
    rows = []
    for index, api_name in enumerate(api_names or ["daily"], start=1):
        rows.append(
            {
                "api_name": api_name,
                "title": api_name,
                "category": "股票数据",
                "description": "",
                "doc_url": f"https://example.com/{index}.md",
                "doc_id": str(index),
                "key": f"{api_name}:{index}",
                "eligibility": "points_ok",
                "required_points": None,
                "permission_note": "",
                "permission_checked_at": "2026-05-29",
            }
        )
    return InterfaceRegistry.from_dicts(rows)


def make_provider(caller, *api_names, data_dir=None):
    with patch.dict(os.environ, {}, clear=True):
        return AShareProvider(
            env_file="/tmp/ashare-data-provider-test-missing.env",
            registry=make_registry(*(api_names or ["daily"])),
            caller=caller,
            data_dir=data_dir,
        )


class ProviderCacheTest(unittest.TestCase):
    def test_local_first_writes_then_reuses_local_data(self) -> None:
        caller = CallbackCaller(
            lambda api_name, params, fields: pd.DataFrame([{"trade_date": params["trade_date"], "close": 1.23}])
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "daily", data_dir=tmp_dir)
            first = provider.call("daily", params={"trade_date": "2026-06-12", "unused": None}, fields="trade_date,close")
            second = provider.call("daily", params={"trade_date": "20260612"}, fields="trade_date, close")

        self.assertEqual(len(caller.calls), 1)
        pd.testing.assert_frame_equal(first, second)

    def test_record_results_keep_list_shape_on_cache_hit(self) -> None:
        caller = CallbackCaller(lambda api_name, params, fields: [{"trade_date": params["trade_date"], "close": 1.23}])
        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "daily", data_dir=tmp_dir)
            first = provider.call("daily", params={"trade_date": "20260612"})
            second = provider.call("daily", params={"trade_date": "20260612"})

        self.assertEqual(first, [{"trade_date": "20260612", "close": 1.23}])
        self.assertEqual(second, first)
        self.assertEqual(len(caller.calls), 1)

    def test_refresh_forces_api_and_overwrites_local_data(self) -> None:
        values = [1.0, 2.0]

        def callback(api_name, params, fields):  # noqa: ANN001
            return pd.DataFrame([{"trade_date": params["trade_date"], "close": values.pop(0)}])

        caller = CallbackCaller(callback)
        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "daily", data_dir=tmp_dir)
            provider.call("daily", params={"trade_date": "20260612"})
            refreshed = provider._call_with_local_store("daily", params={"trade_date": "20260612"}, mode="refresh", data_dir=tmp_dir)

        self.assertEqual(len(caller.calls), 2)
        self.assertEqual(float(refreshed.iloc[0]["close"]), 2.0)

    def test_failed_api_call_is_not_persisted(self) -> None:
        caller = CallbackCaller(lambda api_name, params, fields: (_ for _ in ()).throw(TushareCallError(api_name, "boom")))
        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "daily", data_dir=tmp_dir)
            with self.assertRaises(TushareCallError):
                provider.call("daily", params={"trade_date": "20260612"})
            with self.assertRaises(TushareCallError):
                provider.call("daily", params={"trade_date": "20260612"})

        self.assertEqual(len(caller.calls), 2)

    def test_corrupt_local_data_is_refetched_and_overwritten(self) -> None:
        caller = CallbackCaller(lambda api_name, params, fields: pd.DataFrame([{"trade_date": params["trade_date"], "close": 2.0}]))

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            store.write("daily", {"trade_date": "20260612"}, None, pd.DataFrame([{"trade_date": "20260612", "close": 1.0}]))
            entry = store.entry("daily", {"trade_date": "20260612"})
            entry.data_path.write_text("not parquet", encoding="utf-8")
            provider = make_provider(caller, "daily", data_dir=tmp_dir)

            result = provider.call("daily", params={"trade_date": "20260612"})
            read_back = store.read("daily", {"trade_date": "20260612"})

        self.assertEqual(float(result.iloc[0]["close"]), 2.0)
        self.assertEqual(float(read_back.iloc[0]["close"]), 2.0)
        self.assertEqual(len(caller.calls), 1)

    def test_trade_date_empty_result_is_not_persisted(self) -> None:
        responses = [
            pd.DataFrame(columns=["trade_date", "close"]),
            pd.DataFrame([{"trade_date": "20260612", "close": 2.0}]),
        ]
        caller = CallbackCaller(lambda api_name, params, fields: responses.pop(0))

        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "daily", data_dir=tmp_dir)
            first = provider.call("daily", params={"trade_date": "20260612"})
            second = provider.call("daily", params={"trade_date": "20260612"})

        self.assertTrue(first.empty)
        self.assertEqual(float(second.iloc[0]["close"]), 2.0)
        self.assertEqual(len(caller.calls), 2)

    def test_unpersistable_api_result_is_not_treated_as_success(self) -> None:
        caller = CallbackCaller(lambda api_name, params, fields: {"trade_date": params["trade_date"], "close": 2.0})

        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "daily", data_dir=tmp_dir)
            with self.assertRaises(LocalDataFormatError):
                provider.call("daily", params={"trade_date": "20260612"})

        self.assertEqual(len(caller.calls), 1)

    def test_existing_empty_local_cache_is_refetched(self) -> None:
        caller = CallbackCaller(lambda api_name, params, fields: pd.DataFrame([{"trade_date": params["trade_date"], "close": 2.0}]))

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            store.write("daily", {"trade_date": "20260612"}, None, pd.DataFrame([{"trade_date": "20260612", "close": 1.0}]))
            entry = store.entry("daily", {"trade_date": "20260612"})
            meta = dict(entry.meta)
            meta["rows"] = 0
            entry.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            provider = make_provider(caller, "daily", data_dir=tmp_dir)

            result = provider.call("daily", params={"trade_date": "20260612"})

        self.assertEqual(float(result.iloc[0]["close"]), 2.0)
        self.assertEqual(len(caller.calls), 1)

    def test_non_date_request_bypasses_local_store(self) -> None:
        caller = CallbackCaller(lambda api_name, params, fields: pd.DataFrame([{"ts_code": "000001.SZ"}]))

        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "stock_basic", data_dir=tmp_dir)
            provider.call("stock_basic", params={"exchange": "", "list_status": "L"})
            provider.call("stock_basic", params={"exchange": "", "list_status": "L"})
            store = LocalDataStore(data_dir=tmp_dir)

        self.assertEqual(len(caller.calls), 2)
        self.assertFalse((store.root / "stock_basic").exists())

    def test_local_only_miss_does_not_call_api(self) -> None:
        caller = CallbackCaller(lambda api_name, params, fields: pd.DataFrame())
        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "daily", data_dir=tmp_dir)
            with self.assertRaises(LocalDataMissError):
                provider._call_with_local_store("daily", params={"trade_date": "20260612"}, mode="local_only", data_dir=tmp_dir)

        self.assertEqual(caller.calls, [])

    def test_start_end_date_request_is_split_into_daily_partitions(self) -> None:
        calendar = pd.DataFrame(
            [
                {"cal_date": "20260601", "is_open": 1},
                {"cal_date": "20260602", "is_open": 1},
                {"cal_date": "20260603", "is_open": 1},
            ]
        )
        caller = CallbackCaller(
            lambda api_name, params, fields: calendar.copy()
            if api_name == "trade_cal"
            else pd.DataFrame([{"trade_date": params["trade_date"], "close": 1.0}])
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "daily", "trade_cal", data_dir=tmp_dir)
            first = provider.call("daily", params={"start_date": "20260601", "end_date": "20260603"})
            second = provider.call("daily", params={"start_date": "20260601", "end_date": "20260603"})

        self.assertEqual(
            [call["params"] for call in caller.calls],
            [
                {"exchange": "SSE", "start_date": "20260601", "end_date": "20260603"},
                {"trade_date": "20260601"},
                {"trade_date": "20260602"},
                {"trade_date": "20260603"},
            ],
        )
        self.assertEqual(first["trade_date"].tolist(), ["20260601", "20260602", "20260603"])
        pd.testing.assert_frame_equal(first, second)

    def test_split_record_results_merge_lists_and_refetch_empty_chunks(self) -> None:
        calendar = pd.DataFrame(
            [
                {"cal_date": "20260601", "is_open": 1},
                {"cal_date": "20260602", "is_open": 1},
            ]
        )

        def callback(api_name, params, fields):  # noqa: ANN001
            if api_name == "trade_cal":
                return calendar.copy()
            if params["trade_date"] == "20260601":
                return []
            return [{"trade_date": params["trade_date"], "close": 1.0}]

        caller = CallbackCaller(callback)
        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "daily", "trade_cal", data_dir=tmp_dir)
            first = provider.call("daily", params={"start_date": "20260601", "end_date": "20260602"})
            second = provider.call("daily", params={"start_date": "20260601", "end_date": "20260602"})

        self.assertEqual(first, [{"trade_date": "20260602", "close": 1.0}])
        self.assertEqual(second, first)
        self.assertEqual(
            [call["params"] for call in caller.calls],
            [
                {"exchange": "SSE", "start_date": "20260601", "end_date": "20260602"},
                {"trade_date": "20260601"},
                {"trade_date": "20260602"},
                {"trade_date": "20260601"},
            ],
        )

    def test_range_raises_when_trade_calendar_api_fails_even_if_partitions_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalDataStore(data_dir=tmp_dir)
            store.write("daily", {"trade_date": "20260601"}, None, pd.DataFrame([{"trade_date": "20260601", "close": 1.0}]))
            store.write("daily", {"trade_date": "20260602"}, None, pd.DataFrame([{"trade_date": "20260602", "close": 2.0}]))

            def callback(api_name, params, fields):  # noqa: ANN001
                if api_name == "trade_cal":
                    raise TushareCallError(api_name, "offline")
                raise AssertionError(f"unexpected remote call: {api_name}")

            caller = CallbackCaller(callback)
            provider = make_provider(caller, "daily", "trade_cal", data_dir=tmp_dir)

            with self.assertRaises(TushareCallError):
                provider.call("daily", params={"start_date": "20260601", "end_date": "20260602"})

        self.assertEqual([call["api_name"] for call in caller.calls], ["trade_cal"])

    def test_empty_trade_calendar_result_is_not_persisted(self) -> None:
        responses = [
            pd.DataFrame(columns=["cal_date", "is_open"]),
            pd.DataFrame([{"cal_date": "20260601", "is_open": 1}]),
        ]
        caller = CallbackCaller(lambda api_name, params, fields: responses.pop(0))

        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "trade_cal", data_dir=tmp_dir)
            first = provider.call("trade_cal", params={"exchange": "SSE", "start_date": "20260601", "end_date": "20260601"}, fields="cal_date,is_open")
            second = provider.call("trade_cal", params={"exchange": "SSE", "start_date": "20260601", "end_date": "20260601"}, fields="cal_date,is_open")

        self.assertTrue(first.empty)
        self.assertEqual(second["cal_date"].tolist(), ["20260601"])
        self.assertEqual(len(caller.calls), 2)

    def test_empty_trade_calendar_range_returns_empty_frame(self) -> None:
        calendar = pd.DataFrame(
            [
                {"cal_date": "20260606", "is_open": 0},
                {"cal_date": "20260607", "is_open": 0},
            ]
        )
        caller = CallbackCaller(lambda api_name, params, fields: calendar.copy())

        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = make_provider(caller, "daily", "trade_cal", data_dir=tmp_dir)
            result = provider.call("daily", params={"start_date": "20260606", "end_date": "20260607"})

        self.assertTrue(result.empty)
        self.assertEqual([call["api_name"] for call in caller.calls], ["trade_cal"])


if __name__ == "__main__":
    unittest.main()
