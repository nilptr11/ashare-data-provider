import os
import unittest
from unittest.mock import patch

from tushare_fastcli.provider import (
    STOCK_BASIC_FIELDS,
    TushareInterfaceSelectionError,
    TusharePermissionError,
    TushareProvider,
    TushareUnknownInterfaceError,
)
from tushare_fastcli.registry import InterfaceRegistry


class FakeCaller:
    def __init__(self, result="ok") -> None:
        self.result = result
        self.calls = []

    def call(self, api_name, params=None, fields=None):
        self.calls.append({"api_name": api_name, "params": params, "fields": fields})
        return self.result


def make_registry(*items):
    rows = []
    for item in items:
        rows.append(
            {
                "api_name": item.get("api_name", "daily"),
                "title": item.get("title", item.get("api_name", "daily")),
                "category": item.get("category", "股票数据"),
                "description": "",
                "doc_url": item.get("doc_url", "https://example.com/1.md"),
                "doc_id": item.get("doc_id", "1"),
                "key": item.get("key", f"{item.get('api_name', 'daily')}:1"),
                "eligibility": item.get("eligibility", "points_ok"),
                "required_points": item.get("required_points"),
                "permission_note": "",
                "permission_checked_at": "2026-05-29",
            }
        )
    return InterfaceRegistry.from_dicts(rows)


def make_provider(registry, caller=None, **kwargs):
    with patch.dict(os.environ, {}, clear=True):
        return TushareProvider(
            env_file="/tmp/tushare-fastcli-test-missing.env",
            registry=registry,
            caller=caller or FakeCaller(),
            **kwargs,
        )


class ProviderTest(unittest.TestCase):
    def test_unknown_api_requires_explicit_allow_unknown(self) -> None:
        provider = make_provider(make_registry({"api_name": "daily"}))

        with self.assertRaises(TushareUnknownInterfaceError):
            provider.call("not_exists")

    def test_allow_unknown_still_delegates_to_caller(self) -> None:
        caller = FakeCaller()
        provider = make_provider(make_registry({"api_name": "daily"}), caller=caller)

        provider.call("not_exists", allow_unknown=True, params={"limit": 1})

        self.assertEqual(caller.calls[0]["api_name"], "not_exists")
        self.assertEqual(caller.calls[0]["params"], {"limit": 1})

    def test_blocks_interfaces_requiring_more_points(self) -> None:
        provider = make_provider(
            make_registry({"api_name": "stk_factor_pro", "required_points": 8000}),
            points=2000,
        )

        with self.assertRaises(TusharePermissionError):
            provider.call("stk_factor_pro")

    def test_blocks_separate_permission_by_default(self) -> None:
        provider = make_provider(
            make_registry({"api_name": "hk_daily", "eligibility": "needs_separate_permission"}),
            allow_separate_permission=False,
        )

        with self.assertRaises(TusharePermissionError):
            provider.call("hk_daily")

    def test_duplicate_api_allows_call_when_at_least_one_metadata_entry_is_available(self) -> None:
        caller = FakeCaller()
        provider = make_provider(
            make_registry(
                {
                    "api_name": "stk_mins",
                    "doc_id": "387",
                    "key": "stk_mins:387",
                    "eligibility": "unknown",
                },
                {
                    "api_name": "stk_mins",
                    "doc_id": "370",
                    "key": "stk_mins:370",
                    "eligibility": "needs_separate_permission",
                },
            ),
            caller=caller,
            allow_separate_permission=False,
        )

        provider.call("stk_mins", params={"ts_code": "510300.SH"})

        self.assertEqual(caller.calls[0]["api_name"], "stk_mins")

    def test_doc_id_selection_blocks_selected_restricted_duplicate(self) -> None:
        provider = make_provider(
            make_registry(
                {
                    "api_name": "stk_mins",
                    "doc_id": "387",
                    "key": "stk_mins:387",
                    "eligibility": "unknown",
                },
                {
                    "api_name": "stk_mins",
                    "doc_id": "370",
                    "key": "stk_mins:370",
                    "eligibility": "needs_separate_permission",
                },
            ),
            allow_separate_permission=False,
        )

        with self.assertRaises(TusharePermissionError):
            provider.call("stk_mins", doc_id="370")

    def test_key_selection_reports_missing_metadata(self) -> None:
        provider = make_provider(make_registry({"api_name": "daily", "doc_id": "27", "key": "daily:27"}))

        with self.assertRaises(TushareInterfaceSelectionError):
            provider.call("daily", key="daily:missing")

    def test_use_defaults_merges_configured_params(self) -> None:
        caller = FakeCaller()
        provider = make_provider(make_registry({"api_name": "daily"}), caller=caller)

        provider.call("daily", use_defaults=True, params={"ts_code": "000001.SZ"})

        self.assertEqual(
            caller.calls[0]["params"],
            {"trade_date": "20260423", "ts_code": "000001.SZ"},
        )

    def test_use_defaults_honors_doc_id_selection(self) -> None:
        caller = FakeCaller()
        provider = make_provider(
            make_registry(
                {"api_name": "rt_min", "doc_id": "374", "key": "rt_min:374"},
                {"api_name": "rt_min", "doc_id": "416", "key": "rt_min:416"},
            ),
            caller=caller,
        )

        provider.call("rt_min", doc_id="416", use_defaults=True)

        self.assertEqual(caller.calls[0]["params"], {"ts_code": "510300.SH", "freq": "1MIN"})

    def test_stock_basic_recipe_uses_standard_fields(self) -> None:
        caller = FakeCaller()
        provider = make_provider(make_registry({"api_name": "stock_basic"}), caller=caller)

        provider.stock_basic(list_status="L")

        self.assertEqual(caller.calls[0]["api_name"], "stock_basic")
        self.assertEqual(caller.calls[0]["params"], {"exchange": "", "list_status": "L"})
        self.assertEqual(caller.calls[0]["fields"], STOCK_BASIC_FIELDS)

    def test_latest_trade_date_returns_last_open_day(self) -> None:
        calendar = [
            {"cal_date": "20260531", "is_open": 0},
            {"cal_date": "20260530", "is_open": 0},
            {"cal_date": "20260529", "is_open": 1},
            {"cal_date": "20260528", "is_open": 1},
        ]
        caller = FakeCaller(calendar)
        provider = make_provider(make_registry({"api_name": "trade_cal"}), caller=caller)

        self.assertEqual(provider.latest_trade_date(as_of="2026-05-31"), "20260529")
        self.assertEqual(
            caller.calls[0]["params"],
            {"exchange": "SSE", "start_date": "20260516", "end_date": "20260531"},
        )


if __name__ == "__main__":
    unittest.main()
