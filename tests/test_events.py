import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from ashare_data_provider.events import (
    AStockEventError,
    auto_periods,
    build_forecast_records,
    build_notice_records,
    fetch_notice,
    prepare_forecast,
    prepare_notice,
    validate_period,
)


class EventsTest(unittest.TestCase):
    def test_auto_periods_returns_recent_quarter_ends(self) -> None:
        self.assertEqual(auto_periods(date(2026, 6, 3), count=5), ["20260630", "20260331", "20251231", "20250930", "20250630"])

    def test_validate_period_rejects_non_quarter_end(self) -> None:
        self.assertEqual(validate_period("20260331"), "20260331")
        with self.assertRaises(AStockEventError):
            validate_period("20260228")

    def test_prepare_notice_filters_keyword_and_sorts_by_date(self) -> None:
        df = pd.DataFrame(
            [
                {"代码": "000001", "名称": "平安银行", "公告标题": "年度分红方案", "公告类型": "财务报告", "公告日期": "2026-06-01"},
                {"代码": "000002", "名称": "万科A", "公告标题": "董事会公告", "公告类型": "重大事项", "公告日期": "2026-06-02"},
                {"代码": "000003", "名称": "测试", "公告标题": "季度分红", "公告类型": "财务报告", "公告日期": "2026-05-20"},
            ]
        )

        result = prepare_notice(df, keyword="分红", start=date(2026, 6, 1), end=date(2026, 6, 3))

        self.assertEqual(result["代码"].tolist(), ["000001"])

    def test_build_notice_records_outputs_standard_fields(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "公告标题": "年度分红方案",
                    "公告类型": "财务报告",
                    "公告日期": "2026-06-01",
                    "网址": "https://example.com/a.pdf",
                }
            ]
        )

        records = build_notice_records(df, fetched_at="2026-06-03T10:00:00+08:00")

        self.assertEqual(records[0]["event_type"], "notice")
        self.assertEqual(records[0]["source_kind"], "akshare_notice")
        self.assertEqual(records[0]["stock_code"], "000001")
        self.assertEqual(records[0]["title"], "年度分红方案")
        self.assertEqual(len(records[0]["id"]), 64)
        self.assertEqual(records[0]["raw"]["网址"], "https://example.com/a.pdf")

    def test_fetch_notice_falls_back_when_individual_endpoint_misses_code_column(self) -> None:
        class FakeAk:
            @staticmethod
            def stock_individual_notice_report(**_kwargs):  # noqa: ANN003
                raise KeyError("代码")

            @staticmethod
            def stock_notice_report(**_kwargs):  # noqa: ANN003
                return pd.DataFrame(
                    [
                        {
                            "股票代码": "600475",
                            "股票简称": "华光环能",
                            "公告标题": "年度报告",
                            "公告类型": "财务报告",
                            "公告日期": "2026-06-23",
                            "网址": "https://example.com/600475.pdf",
                        },
                        {
                            "股票代码": "000001",
                            "股票简称": "平安银行",
                            "公告标题": "其他公告",
                            "公告类型": "重大事项",
                            "公告日期": "2026-06-23",
                            "网址": "https://example.com/000001.pdf",
                        },
                    ]
                )

        with patch("ashare_data_provider.events._load_akshare", return_value=(FakeAk, pd)):
            records = fetch_notice(days=1, end_date="20260623", stock="600475")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["stock_code"], "600475")
        self.assertEqual(records[0]["stock_name"], "华光环能")
        self.assertEqual(records[0]["title"], "年度报告")

    def test_fetch_notice_adds_stock_code_when_individual_endpoint_omits_it(self) -> None:
        class FakeAk:
            @staticmethod
            def stock_individual_notice_report(**_kwargs):  # noqa: ANN003
                return pd.DataFrame(
                    [
                        {
                            "公告标题": "个股公告",
                            "公告类型": "重大事项",
                            "公告日期": "2026-06-23",
                            "网址": "https://example.com/notice.pdf",
                        }
                    ]
                )

        with patch("ashare_data_provider.events._load_akshare", return_value=(FakeAk, pd)):
            records = fetch_notice(days=1, end_date="20260623", stock="600475")

        self.assertEqual(records[0]["stock_code"], "600475")
        self.assertEqual(records[0]["title"], "个股公告")

    def test_fetch_notice_returns_empty_when_notice_source_misses_code_column(self) -> None:
        class FakeAk:
            @staticmethod
            def stock_individual_notice_report(**_kwargs):  # noqa: ANN003
                raise KeyError("代码")

            @staticmethod
            def stock_notice_report(**_kwargs):  # noqa: ANN003
                raise KeyError("代码")

        with patch("ashare_data_provider.events._load_akshare", return_value=(FakeAk, pd)):
            records = fetch_notice(days=1, end_date="20260623", stock="600475")

        self.assertEqual(records, [])

    def test_prepare_forecast_filters_stock_keyword_and_sorts(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "报告期": "20260331",
                    "股票代码": "000001",
                    "股票简称": "平安银行",
                    "预测指标": "净利润",
                    "预告类型": "预增",
                    "业绩变动幅度": "20%",
                    "公告日期": "2026-04-01",
                    "业绩变动": "净利润增长",
                    "业绩变动原因": "营收增长",
                },
                {
                    "报告期": "20260331",
                    "股票代码": "000002",
                    "股票简称": "万科A",
                    "预测指标": "净利润",
                    "预告类型": "预减",
                    "业绩变动幅度": "-20%",
                    "公告日期": "2026-04-02",
                    "业绩变动": "净利润下降",
                    "业绩变动原因": "结算减少",
                },
            ]
        )

        result = prepare_forecast(df, stock="000001", keyword="增长", start=date(2026, 4, 1), end=date(2026, 4, 30))

        self.assertEqual(result["股票代码"].tolist(), ["000001"])

    def test_build_forecast_records_outputs_standard_fields(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "报告期": "20260331",
                    "股票代码": "000001",
                    "股票简称": "平安银行",
                    "预测指标": "净利润",
                    "预告类型": "预增",
                    "业绩变动幅度": "20%",
                    "公告日期": "2026-04-01",
                    "业绩变动": "净利润增长",
                    "业绩变动原因": "营收增长",
                }
            ]
        )

        records = build_forecast_records(df, fetched_at="2026-06-03T10:00:00+08:00")

        self.assertEqual(records[0]["event_type"], "forecast")
        self.assertEqual(records[0]["source_kind"], "akshare_yjyg_em")
        self.assertEqual(records[0]["period"], "20260331")
        self.assertEqual(records[0]["forecast_type"], "预增")
        self.assertEqual(len(records[0]["dedupe_key"]), 64)


if __name__ == "__main__":
    unittest.main()
