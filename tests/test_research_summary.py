import unittest

from ashare_data_provider.research_summary import build_research_summary, render_research_summary_markdown


class ResearchSummaryTest(unittest.TestCase):
    def test_build_research_summary_calculates_market_and_fundamental_metrics(self) -> None:
        history = []
        for index in range(20):
            close = 10 + index * 0.1
            history.append(
                {
                    "trade_date": f"202606{index + 1:02d}",
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "close": close,
                    "pre_close": close - 0.1,
                    "vol": 1000 + index,
                }
            )
        context = {
            "schema": "ashare.research_context.v1",
            "generated_at": "2026-06-22T14:00:00+08:00",
            "as_of": "2026-06-22",
            "ts_code": "000001.SZ",
            "symbol": "000001",
            "profile": "full",
            "calendar": {"completed_trade_date": "20260620"},
            "target": {"stock_basic": {"name": "平安银行", "industry": "银行", "market": "主板"}},
            "market": {
                "daily_history": {"records": history},
                "daily_latest": {"records": [{"trade_date": "20260620", "close": 11.9, "pct_chg": 1.2, "amount": 120000.0, "vol": 1019}]},
                "daily_basic_latest": {"records": [{"pe_ttm": 7.5, "pb": 0.8, "total_mv": 1000000.0}]},
                "limit_price_latest": {"records": [{"up_limit": 13.09, "down_limit": 10.71}]},
            },
            "fundamentals": {
                "income": {
                    "records": [
                        {"end_date": "20260331", "ann_date": "20260429", "revenue": 1000000000.0, "n_income_attr_p": 100000000.0},
                        {"end_date": "20251231", "end_type": "4", "revenue": 3800000000.0, "n_income_attr_p": 400000000.0},
                    ]
                },
                "cashflow": {
                    "records": [
                        {"end_date": "20260331", "n_cashflow_act": 50000000.0, "c_fr_sale_sg": 900000000.0},
                        {"end_date": "20251231", "n_cashflow_act": 450000000.0},
                    ]
                },
                "fina_indicator": {
                    "records": [
                        {
                            "end_date": "20260331",
                            "or_yoy": 5.5,
                            "netprofit_yoy": 6.5,
                            "dt_netprofit_yoy": 7.5,
                            "grossprofit_margin": 20.0,
                            "netprofit_margin": 10.0,
                            "debt_to_assets": 55.0,
                            "profit_dedt": 90000000.0,
                        },
                        {
                            "end_date": "20251231",
                            "or_yoy": 4.0,
                            "netprofit_yoy": 3.0,
                            "dt_netprofit_yoy": 2.0,
                        },
                    ]
                },
                "fina_mainbz": {
                    "records": [
                        {"end_date": "20251231", "bz_code": "P", "bz_item": "业务A", "bz_sales": 200000000.0, "bz_profit": 50000000.0},
                        {"end_date": "20251231", "bz_code": "P", "bz_item": "业务B", "bz_sales": 100000000.0, "bz_profit": 10000000.0},
                    ]
                },
                "disclosure_date": {
                    "source": {
                        "kind": "tushare",
                        "api_name": "disclosure_date",
                        "params": {"queries": [{"ts_code": "000001.SZ", "end_date": "20260331"}]},
                    },
                    "records": [{"ts_code": "000001.SZ", "end_date": "20260331", "pre_date": "20260429"}],
                },
            },
            "events": {
                "announcements": {
                    "records": [
                        {"publish_date": "2026-06-01", "title": "关于重大合同中标的公告", "notice_type": "重大合同", "url": "https://example.test/a"}
                    ]
                }
            },
            "source_policy": {
                "dynamic_source_discovery": {
                    "source_classes": [
                        {"id": "official_government_or_regulator"},
                        {"id": "exchange_or_disclosure_platform"},
                        {"id": "listed_company_official"},
                        {"id": "industry_association_or_designated_publisher"},
                        {"id": "commodity_exchange_or_index_provider"},
                    ]
                }
            },
            "external_evidence": [
                {
                    "fact": "行业政策测试事实",
                    "source_class": "official_government_or_regulator",
                    "source_name": "测试官方源",
                    "url": "https://example.test/policy",
                    "query_time": "2026-06-22T14:00:00+08:00",
                    "publish_date": "2026-06-01",
                    "business_segment": "业务A",
                    "supports_need": "industry_policy",
                    "evidence_level": "official_external",
                    "confidence": "high",
                }
            ],
            "data_gaps": [],
            "skipped_sources": [],
        }

        summary = build_research_summary(context)

        self.assertEqual(summary["schema"], "ashare.research_summary.v1")
        self.assertEqual(summary["market"]["ma20"], 10.95)
        self.assertEqual(summary["fundamentals"]["latest_period"]["revenue_100m_yuan"], 10.0)
        self.assertEqual(summary["fundamentals"]["latest_period"]["sales_cash_to_revenue_pct"], 90.0)
        self.assertIn("orders_contracts", summary["events"]["announcement_clues"])
        self.assertEqual(summary["external_evidence"]["valid_count"], 1)
        needs = {need["id"]: need for need in summary["research_needs"]["items"]}
        self.assertEqual(needs["financial_disclosure_schedule"]["status"], "covered_by_project_data")
        self.assertEqual(needs["industry_policy"]["status"], "covered_by_external_evidence")
        self.assertEqual(needs["orders_contracts"]["status"], "has_announcement_clues_needs_filing_extraction")
        self.assertTrue(summary["research_needs"]["analysis_gaps"])

    def test_render_research_summary_markdown_contains_main_sections(self) -> None:
        summary = {
            "target": {"name": "测试公司", "ts_code": "000001.SZ", "industry": "测试", "market": "主板"},
            "generated_from": {"context_generated_at": "2026-06-22T14:00:00+08:00", "completed_trade_date": "20260620"},
            "data_gaps": {"count": 0},
            "market": {"latest_close": 1, "latest_pct_chg": 2, "latest_amount_100m_yuan": 3, "pe_ttm": 4, "pb": 5},
            "fundamentals": {
                "latest_period": {},
                "latest_annual": {},
                "main_business": {"segments": [{"item": "业务A", "sales_100m_yuan": 1.0, "gross_margin_pct": 20.0}]},
            },
            "events": {"announcement_clues": {}},
            "research_needs": {"items": [{"id": "industry_policy", "status": "needs_external_evidence", "question": "政策"}]},
            "external_evidence": {"count": 0, "valid_count": 0},
        }

        markdown = render_research_summary_markdown(summary)

        self.assertIn("## Market", markdown)
        self.assertIn("## Fundamentals", markdown)
        self.assertIn("## Research Needs", markdown)
        self.assertIn("## External Evidence", markdown)
        self.assertIn("业务A", markdown)


if __name__ == "__main__":
    unittest.main()
