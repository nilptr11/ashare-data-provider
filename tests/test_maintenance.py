import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from ashare_data_provider.maintenance import (
    ACCESS_ALLOWED,
    EMPTY_RETRY_AFTER_LAG,
    AccessDecision,
    DatasetSpec,
    MaintenancePlan,
    MartStore,
    PlanDataset,
    RequestVariant,
    audit_access,
    build_maintenance_plan,
    run_backfill,
    run_check,
    run_daily,
    run_status_report,
)
from ashare_data_provider.provider import AShareProvider
from ashare_data_provider.registry import InterfaceRegistry


class FakeCaller:
    def __init__(self):
        self.calls = []

    def call(self, api_name, params=None, fields=None):  # noqa: ANN001
        self.calls.append({"api_name": api_name, "params": params or {}, "fields": fields})
        if api_name == "trade_cal":
            return pd.DataFrame(
                [
                    {"cal_date": "20260601", "is_open": 1},
                    {"cal_date": "20260602", "is_open": 1},
                    {"cal_date": "20260603", "is_open": 0},
                    {"cal_date": "20260623", "is_open": 1},
                    {"cal_date": "20260624", "is_open": 1},
                ]
            )
        if api_name == "daily":
            return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": params["trade_date"], "close": 10.0}])
        if api_name == "limit_list_d":
            return pd.DataFrame()
        if api_name == "stock_basic":
            return pd.DataFrame([{"ts_code": "000001.SZ", "name": "平安银行"}])
        if api_name == "ths_index":
            return pd.DataFrame([{"ts_code": "885800.TI", "name": "机器人", "type": "N"}])
        if api_name == "ths_member":
            return pd.DataFrame(
                [
                    {"ts_code": params["ts_code"], "con_code": "000001.SZ", "con_name": "平安银行"},
                    {"ts_code": params["ts_code"], "con_code": "000002.SZ", "con_name": "万科A"},
                ]
            )
        if api_name == "disclosure_date":
            return pd.DataFrame([{"ts_code": "000001.SZ", "end_date": params["end_date"], "pre_date": "20260430"}])
        if api_name == "income":
            return pd.DataFrame(
                [
                    {"ts_code": params["ts_code"], "end_date": "20260331", "ann_date": "20260420", "revenue": 99.0},
                    {"ts_code": params["ts_code"], "end_date": "20260331", "ann_date": "20260420", "revenue": 100.0},
                ]
            )
        if api_name == "top_list":
            return pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": params["trade_date"], "reason": "日涨幅偏离值达到7%"},
                    {"ts_code": "000001.SZ", "trade_date": params["trade_date"], "reason": "日涨幅偏离值达到7%"},
                ]
            )
        if api_name == "moneyflow_dc":
            return pd.DataFrame(columns=["trade_date", "ts_code", "net_amount"])
        if api_name == "cyq_perf":
            return pd.DataFrame(
                [
                    {"ts_code": params["ts_code"], "trade_date": params["start_date"], "winner_rate": 50.0},
                    {"ts_code": params["ts_code"], "trade_date": params["end_date"], "winner_rate": 51.0},
                ]
            )
        raise AssertionError(f"unexpected api: {api_name}")


def make_registry(*items):
    rows = []
    for index, item in enumerate(items, start=1):
        rows.append(
            {
                "api_name": item["api_name"],
                "title": item.get("title", item["api_name"]),
                "category": "股票数据",
                "description": "",
                "doc_url": f"https://example.com/{index}.md",
                "doc_id": str(index),
                "key": f"{item['api_name']}:{index}",
                "eligibility": item.get("eligibility", "points_ok"),
                "required_points": item.get("required_points"),
                "permission_note": "",
                "permission_checked_at": "2026-06-23",
            }
        )
    return InterfaceRegistry.from_dicts(rows)


def make_provider(registry, caller=None):  # noqa: ANN001
    with patch.dict(os.environ, {}, clear=True):
        return AShareProvider(
            env_file="/tmp/ashare-maintenance-test-missing.env",
            registry=registry,
            caller=caller or FakeCaller(),
            cache_enabled=False,
            points=5000,
        )


class MaintenanceTest(unittest.TestCase):
    def test_plan_filters_denied_and_unknown_without_access_catalog(self) -> None:
        specs = (
            DatasetSpec("daily", "stock_daily", "daily", "日线", "basic", "trade_date", date_param="trade_date"),
            DatasetSpec("stock_basic", "identity", "stock_basic", "股票基础", "basic", "snapshot"),
            DatasetSpec("news", "events", "news", "新闻", "basic", "ann_date", date_param="ann_date"),
        )
        provider = make_provider(
            make_registry(
                {"api_name": "daily", "eligibility": "unknown"},
                {"api_name": "stock_basic", "eligibility": "points_ok", "required_points": 2000},
                {"api_name": "news", "eligibility": "needs_separate_permission"},
            )
        )

        plan = build_maintenance_plan(provider, profile="basic", specs=specs)

        self.assertEqual([item.spec.name for item in plan.datasets], ["stock_basic"])

    def test_plan_uses_access_catalog_for_smoke_verified_unknown_api(self) -> None:
        specs = (DatasetSpec("daily", "stock_daily", "daily", "日线", "basic", "trade_date", date_param="trade_date"),)
        provider = make_provider(make_registry({"api_name": "daily", "eligibility": "unknown"}))
        catalog = {
            "daily": AccessDecision(
                api_name="daily",
                access=ACCESS_ALLOWED,
                source="smoke_verified",
                checked_at="2026-06-23T10:00:00+08:00",
            )
        }

        plan = build_maintenance_plan(provider, profile="basic", specs=specs, access_catalog=catalog)

        self.assertEqual([item.spec.name for item in plan.datasets], ["daily"])

    def test_access_audit_includes_stock_pool_datasets(self) -> None:
        specs = (
            DatasetSpec(
                "cyq_perf",
                "chips",
                "cyq_perf",
                "筹码胜率",
                "full",
                "stock_pool_daily",
                date_param="trade_date",
                requires_stock_pool=True,
            ),
        )
        caller = FakeCaller()
        provider = make_provider(make_registry({"api_name": "cyq_perf", "eligibility": "unknown"}), caller=caller)

        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = audit_access(provider, profile="full", specs=specs, smoke_unknown=True, data_dir=tmp_dir)

        self.assertEqual(payload["interfaces"][0]["api_name"], "cyq_perf")
        self.assertEqual(payload["interfaces"][0]["access"], ACCESS_ALLOWED)
        self.assertEqual(caller.calls[0]["params"]["ts_code"], "000001.SZ")
        self.assertEqual(caller.calls[0]["params"]["start_date"], "20260423")
        self.assertEqual(caller.calls[0]["params"]["end_date"], "20260423")

    def test_stock_pool_dataset_flag_does_not_include_financials(self) -> None:
        specs = (
            DatasetSpec(
                "income",
                "financials",
                "income",
                "利润表",
                "full",
                "stock_pool_financial",
                date_param="period",
                requires_stock_pool=True,
            ),
            DatasetSpec(
                "cyq_perf",
                "chips",
                "cyq_perf",
                "筹码胜率",
                "full",
                "stock_pool_daily",
                date_param="trade_date",
                requires_stock_pool=True,
            ),
        )
        provider = make_provider(
            make_registry(
                {"api_name": "income", "eligibility": "points_ok"},
                {"api_name": "cyq_perf", "eligibility": "points_ok"},
            )
        )

        chips_plan = build_maintenance_plan(provider, profile="full", specs=specs, include_stock_pool_datasets=True)
        financial_plan = build_maintenance_plan(provider, profile="full", specs=specs, include_financials=True)

        self.assertEqual([item.spec.name for item in chips_plan.datasets], ["cyq_perf"])
        self.assertEqual([item.spec.name for item in financial_plan.datasets], ["income"])

    def test_plan_filters_member_dataset_when_driver_is_not_allowed(self) -> None:
        specs = (
            DatasetSpec("ths_index", "membership", "ths_index", "同花顺板块", "full", "snapshot"),
            DatasetSpec(
                "ths_member",
                "membership",
                "ths_member",
                "同花顺成分",
                "full",
                "member_by_index_snapshot",
                driver_dataset="ths_index",
            ),
        )
        provider = make_provider(
            make_registry(
                {"api_name": "ths_index", "eligibility": "unknown"},
                {"api_name": "ths_member", "eligibility": "points_ok", "required_points": 6000},
            )
        )

        plan = build_maintenance_plan(provider, profile="full", specs=specs)

        self.assertEqual([item.spec.name for item in plan.datasets], [])

    def test_plan_includes_project_builtin_without_tushare_metadata(self) -> None:
        specs = (
            DatasetSpec(
                "a_stock_notice",
                "events",
                "a_stock_notice",
                "公告",
                "basic",
                "akshare_notice",
                date_param="publish_date",
                source_kind="project_builtin",
            ),
        )
        provider = make_provider(make_registry({"api_name": "daily", "eligibility": "points_ok"}))

        plan = build_maintenance_plan(provider, profile="basic", specs=specs)

        self.assertEqual([item.spec.name for item in plan.datasets], ["a_stock_notice"])
        self.assertEqual(plan.datasets[0].access.source, "project_builtin")

    def test_backfill_publishes_mart_and_check_reads_partitions(self) -> None:
        specs = (
            DatasetSpec(
                "trade_cal",
                "calendar",
                "trade_cal",
                "交易日历",
                "basic",
                "calendar",
                variants=(RequestVariant("default", {"exchange": "SSE"}),),
            ),
            DatasetSpec("daily", "stock_daily", "daily", "日线", "basic", "trade_date", date_param="trade_date"),
        )
        caller = FakeCaller()
        provider = make_provider(
            make_registry(
                {"api_name": "trade_cal", "eligibility": "points_ok"},
                {"api_name": "daily", "eligibility": "points_ok"},
            ),
            caller=caller,
        )
        plan = build_maintenance_plan(provider, profile="basic", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_backfill(provider, plan, "20260601", "20260603", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            check = run_check(provider, plan, "20260603", trade_days=2, data_dir=tmp_dir)

            self.assertEqual(report["command"], "backfill")
            self.assertTrue(mart.exists("trade_cal", {"exchange": "SSE"}))
            self.assertTrue(mart.exists("daily", {"trade_date": "20260601"}))
            self.assertTrue(mart.exists("daily", {"trade_date": "20260602"}))
            self.assertEqual(check["datasets"][0]["status"], "complete")

    def test_financial_backfill_uses_stock_pool_and_partitions_by_period(self) -> None:
        specs = (
            DatasetSpec(
                "income",
                "financials",
                "income",
                "利润表",
                "full",
                "stock_pool_financial",
                date_param="period",
                requires_stock_pool=True,
            ),
        )
        caller = FakeCaller()
        provider = make_provider(make_registry({"api_name": "income", "eligibility": "points_ok"}), caller=caller)
        plan = build_maintenance_plan(provider, profile="full", specs=specs, include_financials=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_backfill(provider, plan, "20260101", "20260623", data_dir=tmp_dir, stock_pool=["000001.SZ"])
            mart = MartStore(data_dir=tmp_dir)

            self.assertEqual(report["datasets"][0]["status"], "success")
            self.assertTrue(mart.exists("income", {"period": "20260331"}))
            frame = mart.read_dataset("income", {"period": "20260331"})

        self.assertEqual(frame["ts_code"].tolist(), ["000001.SZ"])
        self.assertEqual(frame["period"].tolist(), ["20260331"])
        self.assertEqual(frame["revenue"].tolist(), [100.0])
        self.assertEqual(caller.calls[0]["api_name"], "income")
        self.assertEqual(caller.calls[0]["params"]["ts_code"], "000001.SZ")

    def test_stock_pool_daily_backfill_partitions_by_trade_date(self) -> None:
        specs = (
            DatasetSpec(
                "cyq_perf",
                "chips",
                "cyq_perf",
                "筹码胜率",
                "full",
                "stock_pool_daily",
                date_param="trade_date",
                requires_stock_pool=True,
                unique_key=("ts_code", "trade_date"),
            ),
        )
        caller = FakeCaller()
        provider = make_provider(make_registry({"api_name": "cyq_perf", "eligibility": "points_ok"}), caller=caller)
        plan = build_maintenance_plan(provider, profile="full", specs=specs, include_stock_pool_datasets=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_backfill(provider, plan, "20260622", "20260623", data_dir=tmp_dir, stock_pool=["000001.SZ"])
            mart = MartStore(data_dir=tmp_dir)
            first = mart.read_dataset("cyq_perf", {"trade_date": "20260622"})
            second = mart.read_dataset("cyq_perf", {"trade_date": "20260623"})

        self.assertEqual(report["datasets"][0]["status"], "success")
        self.assertEqual(report["datasets"][0]["partitions_written"], 2)
        self.assertEqual(first["winner_rate"].tolist(), [50.0])
        self.assertEqual(second["winner_rate"].tolist(), [51.0])
        self.assertEqual(caller.calls[0]["params"]["start_date"], "20260622")
        self.assertEqual(caller.calls[0]["params"]["end_date"], "20260623")

    def test_member_snapshot_backfill_uses_driver_codes(self) -> None:
        specs = (
            DatasetSpec("ths_index", "membership", "ths_index", "同花顺板块", "full", "snapshot"),
            DatasetSpec(
                "ths_member",
                "membership",
                "ths_member",
                "同花顺成分",
                "full",
                "member_by_index_snapshot",
                driver_dataset="ths_index",
            ),
        )
        caller = FakeCaller()
        provider = make_provider(
            make_registry(
                {"api_name": "ths_index", "eligibility": "points_ok"},
                {"api_name": "ths_member", "eligibility": "points_ok"},
            ),
            caller=caller,
        )
        plan = build_maintenance_plan(provider, profile="full", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_backfill(provider, plan, "20260623", "20260623", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            frame = mart.read_dataset("ths_member", {"snapshot_date": "20260623"})
            meta = json.loads(mart.meta_path("ths_member", {"snapshot_date": "20260623"}).read_text(encoding="utf-8"))

        self.assertEqual(report["datasets"][1]["status"], "success")
        self.assertEqual(frame["_driver_ts_code"].tolist(), ["885800.TI", "885800.TI"])
        self.assertEqual(frame["_driver_name"].tolist(), ["机器人", "机器人"])
        self.assertEqual(meta["source"]["driver_dataset"], "ths_index")
        self.assertEqual(meta["source"]["driver_count"], 1)
        ths_member_calls = [call for call in caller.calls if call["api_name"] == "ths_member"]
        self.assertEqual(meta["source"]["fetch_mode"], "driver_loop")
        self.assertEqual(ths_member_calls[0]["params"], {})
        self.assertEqual(ths_member_calls[-1]["params"], {"ts_code": "885800.TI"})

    def test_member_snapshot_uses_bulk_call_when_available(self) -> None:
        class BulkMemberCaller(FakeCaller):
            def call(self, api_name, params=None, fields=None):  # noqa: ANN001
                if api_name == "ths_member":
                    self.calls.append({"api_name": api_name, "params": params or {}, "fields": fields})
                    return pd.DataFrame(
                        [
                            {"ts_code": "885800.TI", "con_code": "000001.SZ", "con_name": "平安银行"},
                            {"ts_code": "885800.TI", "con_code": "000002.SZ", "con_name": "万科A"},
                        ]
                    )
                return super().call(api_name, params=params, fields=fields)

        specs = (
            DatasetSpec("ths_index", "membership", "ths_index", "同花顺板块", "full", "snapshot"),
            DatasetSpec(
                "ths_member",
                "membership",
                "ths_member",
                "同花顺成分",
                "full",
                "member_by_index_snapshot",
                driver_dataset="ths_index",
            ),
        )
        caller = BulkMemberCaller()
        provider = make_provider(
            make_registry(
                {"api_name": "ths_index", "eligibility": "points_ok"},
                {"api_name": "ths_member", "eligibility": "points_ok"},
            ),
            caller=caller,
        )
        plan = build_maintenance_plan(provider, profile="full", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            run_backfill(provider, plan, "20260623", "20260623", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            frame = mart.read_dataset("ths_member", {"snapshot_date": "20260623"})
            meta = json.loads(mart.meta_path("ths_member", {"snapshot_date": "20260623"}).read_text(encoding="utf-8"))

        self.assertEqual(frame["_driver_name"].tolist(), ["机器人", "机器人"])
        self.assertEqual(meta["source"]["fetch_mode"], "bulk")
        ths_member_calls = [call for call in caller.calls if call["api_name"] == "ths_member"]
        self.assertEqual(ths_member_calls[0]["params"], {})

    def test_disclosure_date_backfill_partitions_recent_report_periods(self) -> None:
        specs = (
            DatasetSpec(
                "disclosure_date",
                "financials",
                "disclosure_date",
                "财报披露日期",
                "full",
                "financial_disclosure_date",
                date_param="period",
            ),
        )
        caller = FakeCaller()
        provider = make_provider(
            make_registry(
                {"api_name": "disclosure_date", "eligibility": "points_ok"},
                {"api_name": "trade_cal", "eligibility": "points_ok"},
            ),
            caller=caller,
        )
        plan = build_maintenance_plan(provider, profile="full", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_backfill(provider, plan, "20260101", "20260623", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            frame = mart.read_dataset("disclosure_date", {"period": "20260331"})
            check = run_check(provider, plan, "20260623", trade_days=1, data_dir=tmp_dir)

        self.assertEqual(report["datasets"][0]["status"], "success")
        self.assertTrue(frame["period"].eq("20260331").all())
        self.assertIn("20260331", report["datasets"][0]["recent_periods"])
        self.assertEqual(check["datasets"][0]["status"], "complete")

    def test_snapshot_dataset_can_page_until_short_page(self) -> None:
        class PagedCaller(FakeCaller):
            def call(self, api_name, params=None, fields=None):  # noqa: ANN001
                if api_name == "index_member_all":
                    self.calls.append({"api_name": api_name, "params": params or {}, "fields": fields})
                    offset = int((params or {}).get("offset", 0))
                    if offset == 0:
                        return pd.DataFrame(
                            [
                                {"ts_code": "000001.SZ", "l3_code": "801001.SI"},
                                {"ts_code": "000002.SZ", "l3_code": "801001.SI"},
                            ]
                        )
                    return pd.DataFrame([{"ts_code": "000003.SZ", "l3_code": "801002.SI"}])
                return super().call(api_name, params=params, fields=fields)

        specs = (
            DatasetSpec(
                "index_member_all",
                "membership",
                "index_member_all",
                "申万成分",
                "standard",
                "snapshot",
                page_limit=2,
            ),
        )
        caller = PagedCaller()
        provider = make_provider(make_registry({"api_name": "index_member_all", "eligibility": "points_ok"}), caller=caller)
        plan = build_maintenance_plan(provider, profile="standard", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            run_backfill(provider, plan, "20260623", "20260623", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            frame = mart.read_dataset("index_member_all", {"snapshot_date": "20260623"})

        offsets = [call["params"]["offset"] for call in caller.calls if call["api_name"] == "index_member_all"]
        self.assertEqual(offsets, [0, 2])
        self.assertEqual(len(frame), 3)

    def test_snapshot_range_dataset_gets_dynamic_start_and_end_dates(self) -> None:
        class RangeCaller(FakeCaller):
            def call(self, api_name, params=None, fields=None):  # noqa: ANN001
                if api_name == "index_weight":
                    self.calls.append({"api_name": api_name, "params": params or {}, "fields": fields})
                    return pd.DataFrame(
                        [
                            {
                                "index_code": params["index_code"],
                                "con_code": "000001.SZ",
                                "trade_date": "20260620",
                                "weight": 1.0,
                            }
                        ]
                    )
                return super().call(api_name, params=params, fields=fields)

        specs = (
            DatasetSpec(
                "index_weight",
                "membership",
                "index_weight",
                "指数权重",
                "standard",
                "snapshot_range",
                variants=(RequestVariant("hs300", {"index_code": "000300.SH"}),),
                range_lookback_days=10,
            ),
        )
        caller = RangeCaller()
        provider = make_provider(make_registry({"api_name": "index_weight", "eligibility": "points_ok"}), caller=caller)
        plan = build_maintenance_plan(provider, profile="standard", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            run_backfill(provider, plan, "20260618", "20260623", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            self.assertTrue(mart.exists("index_weight", {"snapshot_date": "20260623"}))

        params = caller.calls[0]["params"]
        self.assertEqual(params["start_date"], "20260613")
        self.assertEqual(params["end_date"], "20260623")
        self.assertEqual(params["index_code"], "000300.SH")

    def test_quality_rules_flag_low_rows_duplicates_and_stale_dates(self) -> None:
        class BadDailyCaller(FakeCaller):
            def call(self, api_name, params=None, fields=None):  # noqa: ANN001
                if api_name == "daily":
                    self.calls.append({"api_name": api_name, "params": params or {}, "fields": fields})
                    return pd.DataFrame(
                        [
                            {"ts_code": "000001.SZ", "trade_date": "20260531", "close": 10.0},
                            {"ts_code": "000001.SZ", "trade_date": "20260531", "close": 11.0},
                        ]
                    )
                return super().call(api_name, params=params, fields=fields)

        specs = (
            DatasetSpec(
                "daily",
                "stock_daily",
                "daily",
                "日线",
                "basic",
                "trade_date",
                date_param="trade_date",
                required_columns=("ts_code", "trade_date", "close"),
                unique_key=("ts_code", "trade_date"),
                min_rows=3,
            ),
        )
        provider = make_provider(
            make_registry({"api_name": "daily", "eligibility": "points_ok"}, {"api_name": "trade_cal", "eligibility": "points_ok"}),
            caller=BadDailyCaller(),
        )
        plan = build_maintenance_plan(provider, profile="basic", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_backfill(provider, plan, "20260601", "20260601", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            meta = json.loads(mart.meta_path("daily", {"trade_date": "20260601"}).read_text(encoding="utf-8"))
            check = run_check(provider, plan, "20260601", trade_days=1, data_dir=tmp_dir)

        issue_types = {issue["type"] for issue in meta["quality"]["issues"]}
        self.assertEqual(report["datasets"][0]["status"], "partial")
        self.assertEqual(meta["quality_status"], "anomalous_rows")
        self.assertIn("anomalous_rows", issue_types)
        self.assertIn("duplicate_key", issue_types)
        self.assertIn("stale_data", issue_types)
        self.assertEqual(check["datasets"][0]["status"], "needs_retry")
        self.assertEqual(check["datasets"][0]["quality_issues"][0]["quality_status"], "anomalous_rows")

    def test_limit_list_d_uses_akshare_fallback_when_tushare_is_empty(self) -> None:
        specs = (
            DatasetSpec("limit_list_d", "short_term", "limit_list_d", "涨跌停池", "full", "trade_date", date_param="trade_date"),
        )
        provider = make_provider(
            make_registry({"api_name": "limit_list_d", "eligibility": "points_ok"}, {"api_name": "trade_cal", "eligibility": "points_ok"}),
            caller=FakeCaller(),
        )
        plan = build_maintenance_plan(provider, profile="full", specs=specs)

        class FakeAk:
            @staticmethod
            def stock_zt_pool_em(date):  # noqa: ANN001
                return pd.DataFrame([{"代码": "000001", "名称": "平安银行", "最新价": 10.0}])

            @staticmethod
            def stock_zt_pool_zbgc_em(date):  # noqa: ANN001
                return pd.DataFrame([{"代码": "000002", "名称": "万科A", "最新价": 9.0}])

            @staticmethod
            def stock_zt_pool_dtgc_em(date):  # noqa: ANN001
                return pd.DataFrame([{"代码": "600000", "名称": "浦发银行", "最新价": 8.0}])

        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict("sys.modules", {"akshare": FakeAk}):
            report = run_backfill(provider, plan, "20260601", "20260601", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            frame = mart.read_dataset("limit_list_d", {"trade_date": "20260601"})
            meta = json.loads(mart.meta_path("limit_list_d", {"trade_date": "20260601"}).read_text(encoding="utf-8"))

        self.assertEqual(report["datasets"][0]["rows"], 3)
        self.assertEqual(set(frame["limit"].tolist()), {"U", "Z", "D"})
        self.assertIn("000001.SZ", frame["ts_code"].tolist())
        self.assertEqual(meta["source"]["kind"], "akshare")
        self.assertEqual(meta["source"]["fallback_for"], "tushare.limit_list_d")

    def test_event_news_backfill_keeps_successful_sources_when_one_fails(self) -> None:
        class FakeNewsProvider:
            _env_file = "/tmp/ashare-maintenance-test-missing.env"

            def event_news(self, sources=None, anchor_date=None):  # noqa: ANN001
                source = sources[0]
                if source == "wallstreetcn":
                    raise RuntimeError("network failed")
                if source == "xq":
                    return [
                        {
                            "source_slug": source,
                            "date": "2026-06-22 09:30:00",
                            "content": "news",
                            "dedupe_key": "xq-1",
                        }
                    ]
                return []

        spec = DatasetSpec(
            "event_news",
            "news",
            "event_news",
            "新闻快讯",
            "full",
            "event_news",
            date_param="news_date",
            source_kind="project_builtin",
        )
        plan = MaintenancePlan(
            profile="full",
            generated_at="2026-06-23T00:00:00+08:00",
            datasets=(
                PlanDataset(
                    spec=spec,
                    access=AccessDecision(api_name="event_news", access=ACCESS_ALLOWED, source="project_builtin"),
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_backfill(FakeNewsProvider(), plan, "20260622", "20260622", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            frame = mart.read_dataset("event_news", {"news_date": "2026-06-22"})

        self.assertEqual(report["datasets"][0]["status"], "partial")
        self.assertEqual(report["datasets"][0]["rows"], 1)
        self.assertEqual(report["datasets"][0]["sources_requested"], 9)
        self.assertEqual(len(report["datasets"][0]["errors"]), 1)
        self.assertEqual(frame["source_slug"].tolist(), ["xq"])

    def test_notice_backfill_keeps_successful_dates_when_one_date_fails(self) -> None:
        class FakeNoticeProvider:
            _env_file = "/tmp/ashare-maintenance-test-missing.env"

            def a_stock_notice(self, days=1, end_date=None, category="全部"):  # noqa: ANN001
                if str(end_date) == "20260622":
                    raise RuntimeError("ssl failed")
                return [
                    {
                        "id": "notice-1",
                        "dedupe_key": "notice-1",
                        "content_hash": "hash-1",
                        "event_type": "notice",
                        "publish_date": "2026-06-23",
                        "title": "公告",
                    }
                ]

        spec = DatasetSpec(
            "a_stock_notice",
            "events",
            "a_stock_notice",
            "公告",
            "standard",
            "akshare_notice",
            date_param="publish_date",
            source_kind="project_builtin",
        )
        plan = MaintenancePlan(
            profile="standard",
            generated_at="2026-06-23T00:00:00+08:00",
            datasets=(
                PlanDataset(
                    spec=spec,
                    access=AccessDecision(api_name="a_stock_notice", access=ACCESS_ALLOWED, source="project_builtin"),
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_backfill(FakeNoticeProvider(), plan, "20260622", "20260623", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            frame = mart.read_dataset("a_stock_notice", {"publish_date": "2026-06-23"})

        self.assertEqual(report["datasets"][0]["status"], "partial")
        self.assertEqual(report["datasets"][0]["failed_partitions"], ["2026-06-22"])
        self.assertEqual(frame["title"].tolist(), ["公告"])

    def test_earnings_forecast_backfill_writes_empty_event_partitions(self) -> None:
        class FakeForecastProvider:
            _env_file = "/tmp/ashare-maintenance-test-missing.env"

            def earnings_forecast(self, **kwargs):  # noqa: ANN003
                return [
                    {
                        "id": "forecast-1",
                        "dedupe_key": "forecast-1",
                        "content_hash": "hash-1",
                        "event_type": "forecast",
                        "publish_date": "2026-06-23",
                        "stock_code": "000001",
                        "period": "20260630",
                    }
                ]

        spec = DatasetSpec(
            "earnings_forecast",
            "events",
            "earnings_forecast",
            "业绩预告",
            "standard",
            "akshare_forecast",
            date_param="publish_date",
            source_kind="project_builtin",
        )
        plan = MaintenancePlan(
            profile="standard",
            generated_at="2026-06-23T00:00:00+08:00",
            datasets=(
                PlanDataset(
                    spec=spec,
                    access=AccessDecision(api_name="earnings_forecast", access=ACCESS_ALLOWED, source="project_builtin"),
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_backfill(FakeForecastProvider(), plan, "20260622", "20260623", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            empty_exists = mart.exists("earnings_forecast", {"publish_date": "2026-06-22"})
            empty_frame = mart.read_dataset("earnings_forecast", {"publish_date": "2026-06-22"})
            record_frame = mart.read_dataset("earnings_forecast", {"publish_date": "2026-06-23"})

        self.assertEqual(report["datasets"][0]["status"], "success")
        self.assertTrue(empty_exists)
        self.assertEqual(len(empty_frame), 0)
        self.assertEqual(record_frame["stock_code"].tolist(), ["000001"])

    def test_daily_can_force_explicit_end_date(self) -> None:
        specs = (
            DatasetSpec("daily", "stock_daily", "daily", "日线", "basic", "trade_date", date_param="trade_date"),
        )
        caller = FakeCaller()
        provider = make_provider(
            make_registry({"api_name": "daily", "eligibility": "points_ok"}, {"api_name": "trade_cal", "eligibility": "points_ok"}),
            caller=caller,
        )
        plan = build_maintenance_plan(provider, profile="basic", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_daily(provider, plan, as_of="20260624", end_date="20260624", lookback_days=2, data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)

            self.assertTrue(mart.exists("daily", {"trade_date": "20260624"}))

        daily_dates = [call["params"].get("trade_date") for call in caller.calls if call["api_name"] == "daily"]
        self.assertIn("20260624", daily_dates)
        self.assertEqual(report["completed_trade_date"], "20260624")
        self.assertEqual(report["target_trade_date_source"], "explicit_end_date")

    def test_daily_defaults_to_previous_trade_date_before_evening_cutoff(self) -> None:
        specs = (
            DatasetSpec("daily", "stock_daily", "daily", "日线", "basic", "trade_date", date_param="trade_date"),
        )
        caller = FakeCaller()
        provider = make_provider(
            make_registry({"api_name": "daily", "eligibility": "points_ok"}, {"api_name": "trade_cal", "eligibility": "points_ok"}),
            caller=caller,
        )
        plan = build_maintenance_plan(provider, profile="basic", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_daily(provider, plan, as_of=datetime(2026, 6, 24, 19, 59), lookback_days=2, data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)

            self.assertTrue(mart.exists("daily", {"trade_date": "20260623"}))
            self.assertFalse(mart.exists("daily", {"trade_date": "20260624"}))

        self.assertEqual(report["completed_trade_date"], "20260623")
        self.assertEqual(report["target_trade_date_source"], "daily_completion_cutoff")
        self.assertEqual(report["daily_completion_cutoff"], "20:00")

    def test_daily_defaults_to_today_after_evening_cutoff_when_today_is_open(self) -> None:
        specs = (
            DatasetSpec("daily", "stock_daily", "daily", "日线", "basic", "trade_date", date_param="trade_date"),
        )
        caller = FakeCaller()
        provider = make_provider(
            make_registry({"api_name": "daily", "eligibility": "points_ok"}, {"api_name": "trade_cal", "eligibility": "points_ok"}),
            caller=caller,
        )
        plan = build_maintenance_plan(provider, profile="basic", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = run_daily(provider, plan, as_of=datetime(2026, 6, 24, 20, 0), lookback_days=2, data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)

            self.assertTrue(mart.exists("daily", {"trade_date": "20260624"}))

        self.assertEqual(report["completed_trade_date"], "20260624")
        self.assertEqual(report["target_trade_date_source"], "daily_completion_cutoff")

    def test_backfill_drops_exact_duplicate_rows_before_writing_partition(self) -> None:
        specs = (
            DatasetSpec("top_list", "short_term", "top_list", "龙虎榜", "standard", "trade_date", date_param="trade_date"),
        )
        provider = make_provider(
            make_registry({"api_name": "top_list", "eligibility": "points_ok"}, {"api_name": "trade_cal", "eligibility": "points_ok"}),
            caller=FakeCaller(),
        )
        plan = build_maintenance_plan(provider, profile="standard", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            run_backfill(provider, plan, "20260601", "20260601", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            frame = mart.read_dataset("top_list", {"trade_date": "20260601"})

        self.assertEqual(len(frame), 1)

    def test_historical_empty_partition_is_suspicious_and_retried(self) -> None:
        specs = (
            DatasetSpec(
                "moneyflow_dc",
                "moneyflow",
                "moneyflow_dc",
                "个股资金流 DC",
                "standard",
                "trade_date",
                date_param="trade_date",
                empty_policy=EMPTY_RETRY_AFTER_LAG,
                empty_lag_days=2,
            ),
        )
        caller = FakeCaller()
        provider = make_provider(
            make_registry({"api_name": "moneyflow_dc", "eligibility": "points_ok"}, {"api_name": "trade_cal", "eligibility": "points_ok"}),
            caller=caller,
        )
        plan = build_maintenance_plan(provider, profile="standard", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            first = run_backfill(provider, plan, "20260601", "20260601", data_dir=tmp_dir)
            second = run_backfill(provider, plan, "20260601", "20260601", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            meta = json.loads(mart.meta_path("moneyflow_dc", {"trade_date": "20260601"}).read_text(encoding="utf-8"))
            check = run_check(provider, plan, "20260601", trade_days=1, data_dir=tmp_dir)

        moneyflow_calls = [call for call in caller.calls if call["api_name"] == "moneyflow_dc"]
        self.assertEqual(len(moneyflow_calls), 2)
        self.assertEqual(first["datasets"][0]["status"], "partial")
        self.assertEqual(second["datasets"][0]["quality_retries"], 1)
        self.assertEqual(meta["quality_status"], "suspicious_empty")
        self.assertEqual(meta["columns"], ["trade_date", "ts_code", "net_amount"])
        self.assertEqual(check["datasets"][0]["status"], "needs_retry")
        self.assertEqual(check["datasets"][0]["quality_issues"][0]["date"], "20260601")

    def test_current_empty_partition_is_pending_and_cached_within_lag(self) -> None:
        specs = (
            DatasetSpec(
                "moneyflow_dc",
                "moneyflow",
                "moneyflow_dc",
                "个股资金流 DC",
                "standard",
                "trade_date",
                date_param="trade_date",
                empty_policy=EMPTY_RETRY_AFTER_LAG,
                empty_lag_days=2,
            ),
        )
        caller = FakeCaller()
        provider = make_provider(
            make_registry({"api_name": "moneyflow_dc", "eligibility": "points_ok"}, {"api_name": "trade_cal", "eligibility": "points_ok"}),
            caller=caller,
        )
        plan = build_maintenance_plan(provider, profile="standard", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            first = run_backfill(provider, plan, "20260623", "20260623", data_dir=tmp_dir)
            second = run_backfill(provider, plan, "20260623", "20260623", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            meta = json.loads(mart.meta_path("moneyflow_dc", {"trade_date": "20260623"}).read_text(encoding="utf-8"))
            check = run_check(provider, plan, "20260623", trade_days=1, data_dir=tmp_dir)

        moneyflow_calls = [call for call in caller.calls if call["api_name"] == "moneyflow_dc"]
        self.assertEqual(len(moneyflow_calls), 1)
        self.assertEqual(first["datasets"][0]["status"], "success")
        self.assertEqual(second["datasets"][0]["cached_partitions"], 1)
        self.assertEqual(meta["quality_status"], "pending_empty")
        self.assertEqual(check["datasets"][0]["status"], "complete")
        self.assertEqual(check["datasets"][0]["pending_empty_partitions"][0]["date"], "20260623")

    def test_retryable_empty_partition_reports_success_after_repair(self) -> None:
        class FlakyMoneyflowCaller(FakeCaller):
            def __init__(self):
                super().__init__()
                self.moneyflow_calls = 0

            def call(self, api_name, params=None, fields=None):  # noqa: ANN001
                if api_name == "moneyflow_dc":
                    self.calls.append({"api_name": api_name, "params": params or {}, "fields": fields})
                    self.moneyflow_calls += 1
                    if self.moneyflow_calls == 1:
                        return pd.DataFrame(columns=["trade_date", "ts_code", "net_amount"])
                    return pd.DataFrame([{"trade_date": params["trade_date"], "ts_code": "000001.SZ", "net_amount": 1.0}])
                return super().call(api_name, params=params, fields=fields)

        specs = (
            DatasetSpec(
                "moneyflow_dc",
                "moneyflow",
                "moneyflow_dc",
                "个股资金流 DC",
                "standard",
                "trade_date",
                date_param="trade_date",
                empty_policy=EMPTY_RETRY_AFTER_LAG,
                empty_lag_days=2,
            ),
        )
        caller = FlakyMoneyflowCaller()
        provider = make_provider(
            make_registry({"api_name": "moneyflow_dc", "eligibility": "points_ok"}, {"api_name": "trade_cal", "eligibility": "points_ok"}),
            caller=caller,
        )
        plan = build_maintenance_plan(provider, profile="standard", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            first = run_backfill(provider, plan, "20260601", "20260601", data_dir=tmp_dir)
            second = run_backfill(provider, plan, "20260601", "20260601", data_dir=tmp_dir)
            mart = MartStore(data_dir=tmp_dir)
            meta = json.loads(mart.meta_path("moneyflow_dc", {"trade_date": "20260601"}).read_text(encoding="utf-8"))

        self.assertEqual(first["datasets"][0]["status"], "partial")
        self.assertEqual(second["datasets"][0]["status"], "success")
        self.assertEqual(second["datasets"][0]["quality_retries"], 1)
        self.assertEqual(second["datasets"][0]["quality_issues"], [])
        self.assertEqual(meta["quality_status"], "ok")

    def test_check_includes_event_partition_coverage(self) -> None:
        specs = (
            DatasetSpec(
                "trade_cal",
                "calendar",
                "trade_cal",
                "交易日历",
                "basic",
                "calendar",
                variants=(RequestVariant("default", {"exchange": "SSE"}),),
            ),
            DatasetSpec(
                "a_stock_notice",
                "events",
                "a_stock_notice",
                "公告",
                "standard",
                "akshare_notice",
                date_param="publish_date",
                source_kind="project_builtin",
            ),
        )
        provider = make_provider(make_registry({"api_name": "trade_cal", "eligibility": "points_ok"}))
        plan = build_maintenance_plan(provider, profile="standard", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            mart = MartStore(data_dir=tmp_dir)
            mart.write(
                "trade_cal",
                {"exchange": "SSE"},
                pd.DataFrame([{"cal_date": "20260602", "is_open": 1}, {"cal_date": "20260623", "is_open": 1}]),
            )
            mart.write(
                "a_stock_notice",
                {"publish_date": "2026-06-22"},
                pd.DataFrame([{"id": "notice-1", "publish_date": "2026-06-22"}]),
            )
            check = run_check(provider, plan, "20260623", trade_days=2, event_days=2, data_dir=tmp_dir)

        by_name = {item["name"]: item for item in check["datasets"]}
        self.assertEqual(by_name["trade_cal"]["status"], "complete")
        self.assertEqual(by_name["a_stock_notice"]["status"], "needs_retry")
        self.assertEqual(by_name["a_stock_notice"]["expected_count"], 2)
        self.assertEqual(by_name["a_stock_notice"]["available_count"], 1)
        self.assertEqual(by_name["a_stock_notice"]["missing_partitions"], ["2026-06-23"])

    def test_status_report_summarizes_analysis_readiness(self) -> None:
        specs = (
            DatasetSpec(
                "trade_cal",
                "calendar",
                "trade_cal",
                "交易日历",
                "basic",
                "calendar",
                variants=(RequestVariant("default", {"exchange": "SSE"}),),
            ),
            DatasetSpec("daily", "stock_daily", "daily", "日线", "basic", "trade_date", date_param="trade_date"),
        )
        provider = make_provider(
            make_registry(
                {"api_name": "trade_cal", "eligibility": "points_ok"},
                {"api_name": "daily", "eligibility": "points_ok"},
            )
        )
        plan = build_maintenance_plan(provider, profile="basic", specs=specs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            mart = MartStore(data_dir=tmp_dir)
            mart.write(
                "trade_cal",
                {"exchange": "SSE"},
                pd.DataFrame([{"cal_date": "20260601", "is_open": 1}]),
            )
            mart.write(
                "daily",
                {"trade_date": "20260601"},
                pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260601", "close": 10.0}]),
            )
            report = run_status_report(provider, plan, "20260601", trade_days=1, event_days=1, data_dir=tmp_dir, write_report_file=False)

        self.assertEqual(report["summary"]["datasets_total"], 2)
        self.assertTrue(report["analysis_ready"]["ready"])
        self.assertEqual(report["analysis_ready"]["level"], "ready")
        self.assertIn("after_close_initial", report["schedule_recommendation"])


if __name__ == "__main__":
    unittest.main()
