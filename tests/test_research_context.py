import unittest

import pandas as pd

from ashare_data_provider.research_context import build_research_context


class FakeResearchProvider:
    def __init__(self) -> None:
        self.calls = []
        self.news_calls = []

    def previous_trade_date(self, as_of=None):  # noqa: ANN001
        self.calls.append(("previous_trade_date", as_of))
        return "20260601"

    def stock_basic(self):
        self.calls.append(("stock_basic", None))
        return pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "industry": "银行"},
                {"ts_code": "000002.SZ", "symbol": "000002", "name": "万科A", "industry": "全国地产"},
            ]
        )

    def call(self, api_name, params=None, fields=None):  # noqa: ANN001
        self.calls.append((api_name, dict(params or {}), fields))
        if api_name == "income":
            raise RuntimeError("income unavailable")
        if api_name == "daily":
            return pd.DataFrame(
                [
                    {"ts_code": params["ts_code"], "trade_date": "20260601", "open": 10.0, "close": 10.5, "vol": 1000},
                ]
            )
        if api_name == "daily_basic":
            return pd.DataFrame(
                [
                    {"ts_code": params["ts_code"], "trade_date": "20260601", "pe_ttm": 7.5, "pb": 0.8},
                ]
            )
        if api_name == "disclosure_date":
            return pd.DataFrame(
                [
                    {"ts_code": params["ts_code"], "end_date": params["end_date"], "pre_date": "20260820"},
                ]
            )
        return pd.DataFrame()

    def a_stock_notice(self, **kwargs):  # noqa: ANN003
        self.calls.append(("a_stock_notice", kwargs))
        return [{"event_type": "notice", "stock_code": kwargs["stock"], "title": "年度报告"}]

    def earnings_forecast(self, **kwargs):  # noqa: ANN003
        self.calls.append(("earnings_forecast", kwargs))
        return [{"event_type": "forecast", "stock_code": kwargs["stock"], "forecast_type": "预增"}]

    def event_news(self, **kwargs):  # noqa: ANN003
        self.news_calls.append(kwargs)
        return [{"src": "cls", "content": "测试快讯"}]


class ResearchContextTest(unittest.TestCase):
    def test_build_research_context_collects_core_sections_and_gaps(self) -> None:
        provider = FakeResearchProvider()

        context = build_research_context(
            "000001.SZ",
            as_of="2026-06-02",
            profile="standard",
            lookback_days=30,
            provider=provider,
            max_rows_per_dataset=5,
        )

        self.assertEqual(context["schema"], "ashare.research_context.v1")
        self.assertEqual(context["calendar"]["completed_trade_date"], "20260601")
        self.assertEqual(context["target"]["stock_basic"]["name"], "平安银行")
        self.assertEqual(context["market"]["daily_history"]["records"][0]["close"], 10.5)
        self.assertEqual(context["market"]["daily_basic_latest"]["records"][0]["pe_ttm"], 7.5)
        self.assertEqual(context["events"]["announcements"]["records"][0]["title"], "年度报告")
        self.assertEqual(context["events"]["earnings_forecast"]["records"][0]["forecast_type"], "预增")
        self.assertTrue(any(gap["name"] == "income" and gap["status"] == "unavailable" for gap in context["data_gaps"]))
        self.assertTrue(any(source["api_name"] == "news" for source in context["skipped_sources"]))
        self.assertIn("dynamic_source_discovery", context["source_policy"])
        self.assertFalse(any(call[0] == "forecast" for call in provider.calls))
        self.assertEqual(provider.news_calls, [])

    def test_build_research_context_can_include_news_when_requested(self) -> None:
        provider = FakeResearchProvider()

        context = build_research_context(
            "000001.SZ",
            as_of="2026-06-02",
            profile="basic",
            include_news=True,
            news_sources=["cls"],
            provider=provider,
            max_rows_per_dataset=5,
        )

        self.assertEqual(context["events"]["event_news"]["records"][0]["src"], "cls")
        self.assertEqual(provider.news_calls[0]["sources"], ["cls"])

    def test_basic_profile_skips_slow_event_and_fundamental_sources(self) -> None:
        provider = FakeResearchProvider()

        context = build_research_context(
            "000001.SZ",
            as_of="2026-06-02",
            profile="basic",
            provider=provider,
            max_rows_per_dataset=5,
        )

        self.assertIn("daily_latest", context["market"])
        self.assertNotIn("daily_history", context["market"])
        self.assertNotIn("announcements", context["events"])
        self.assertNotIn("income", context["fundamentals"])
        self.assertFalse(any(call[0] == "a_stock_notice" for call in provider.calls))

    def test_full_profile_collects_supplemental_financial_sources(self) -> None:
        provider = FakeResearchProvider()

        build_research_context(
            "000001.SZ",
            as_of="2026-06-02",
            profile="full",
            provider=provider,
            max_rows_per_dataset=5,
        )

        api_names = [call[0] for call in provider.calls]
        self.assertIn("disclosure_date", api_names)
        self.assertIn("dividend", api_names)
        self.assertIn("fina_audit", api_names)
        disclosure_calls = [call for call in provider.calls if call[0] == "disclosure_date"]
        self.assertGreaterEqual(len(disclosure_calls), 4)
        self.assertEqual(disclosure_calls[0][1]["end_date"], "20260331")

    def test_build_research_context_rejects_invalid_windows(self) -> None:
        with self.assertRaises(ValueError):
            build_research_context("000001.SZ", lookback_days=0, provider=FakeResearchProvider())

        with self.assertRaises(ValueError):
            build_research_context("000001.SZ", profile="slow", provider=FakeResearchProvider())


if __name__ == "__main__":
    unittest.main()
