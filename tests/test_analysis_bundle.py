import tempfile
import unittest

import pandas as pd

from ashare_data_provider.analysis_bundle import build_market_analysis_bundle
from ashare_data_provider.maintenance import ACCESS_ALLOWED, AccessDecision, DatasetSpec, MaintenancePlan, MartStore, PlanDataset


def make_plan(names):
    return MaintenancePlan(
        profile="full",
        generated_at="2026-06-23T00:00:00+08:00",
        datasets=tuple(
            PlanDataset(
                spec=DatasetSpec(name, "test", name, name, "basic", "trade_date"),
                access=AccessDecision(api_name=name, access=ACCESS_ALLOWED, source="test"),
            )
            for name in names
        ),
    )


class AnalysisBundleTest(unittest.TestCase):
    def test_market_bundle_reads_window_and_today_slices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mart = MartStore(data_dir=tmp_dir)
            mart.write(
                "trade_cal",
                {"exchange": "SSE"},
                pd.DataFrame(
                    [
                        {"cal_date": "20260620", "is_open": 0},
                        {"cal_date": "20260622", "is_open": 1},
                        {"cal_date": "20260623", "is_open": 1},
                    ]
                ),
            )
            mart.write(
                "stock_basic",
                {"snapshot_date": "20260623"},
                pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "symbol": "000001",
                            "name": "平安银行",
                            "industry": "银行",
                            "market": "主板",
                            "exchange": "SZSE",
                            "list_status": "L",
                        }
                    ]
                ),
            )
            for trade_date, pct_chg, amount in [("20260622", 1.0, 1000.0), ("20260623", 2.0, 3000.0)]:
                mart.write(
                    "daily",
                    {"trade_date": trade_date},
                    pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": trade_date, "close": 10.0, "pct_chg": pct_chg, "amount": amount}]),
                )
                mart.write(
                    "daily_basic",
                    {"trade_date": trade_date},
                    pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": trade_date, "turnover_rate": 3.0, "pe_ttm": 8.0, "pb": 1.0}]),
                )
                mart.write(
                    "adj_factor",
                    {"trade_date": trade_date},
                    pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": trade_date, "adj_factor": 1.0}]),
                )
                mart.write(
                    "stk_limit",
                    {"trade_date": trade_date},
                    pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": trade_date, "up_limit": 11.0, "down_limit": 9.0}]),
                )
                mart.write(
                    "index_daily",
                    {"trade_date": trade_date},
                    pd.DataFrame([{"ts_code": "000001.SH", "trade_date": trade_date, "pct_chg": pct_chg}]),
                )
                mart.write(
                    "index_dailybasic",
                    {"trade_date": trade_date},
                    pd.DataFrame([{"ts_code": "000001.SH", "trade_date": trade_date, "pe": 12.0}]),
                )
                mart.write(
                    "sw_daily",
                    {"trade_date": trade_date},
                    pd.DataFrame([{"ts_code": "801780.SI", "trade_date": trade_date, "name": "银行", "pct_chg": pct_chg}]),
                )
                mart.write(
                    "ci_daily",
                    {"trade_date": trade_date},
                    pd.DataFrame([{"ts_code": "CI005001", "trade_date": trade_date, "name": "银行", "pct_chg": pct_chg}]),
                )
            mart.write("moneyflow", {"trade_date": "20260623"}, pd.DataFrame([{"ts_code": "000001.SZ", "net_mf_amount": 5.0}]))
            mart.write("moneyflow_dc", {"trade_date": "20260623"}, pd.DataFrame([{"ts_code": "000001.SZ", "net_amount": 6.0}]))
            mart.write("moneyflow_ths", {"trade_date": "20260623"}, pd.DataFrame([{"ts_code": "000001.SZ", "main_net_amt": 7.0}]))
            mart.write("moneyflow_ind_ths", {"trade_date": "20260623"}, pd.DataFrame([{"name": "银行", "net_amount": 10.0}]))
            mart.write("moneyflow_ind_dc", {"trade_date": "20260623"}, pd.DataFrame([{"name": "银行", "net_amount": 11.0}]))
            mart.write("moneyflow_cnt_ths", {"trade_date": "20260623"}, pd.DataFrame([{"name": "大金融", "net_amount": 20.0}]))
            mart.write("top_list", {"trade_date": "20260623"}, pd.DataFrame([{"ts_code": "000001.SZ", "net_amount": 100.0}]))
            mart.write("margin_detail", {"trade_date": "20260623"}, pd.DataFrame([{"ts_code": "000001.SZ", "rzye": 1000.0}]))
            mart.write(
                "limit_list_d",
                {"trade_date": "20260623"},
                pd.DataFrame(
                    [
                        {"ts_code": "000001.SZ", "limit": "U"},
                        {"ts_code": "000002.SZ", "limit": "D"},
                        {"ts_code": "000003.SZ", "limit": "Z"},
                    ]
                ),
            )
            mart.write("limit_step", {"trade_date": "20260623"}, pd.DataFrame([{"ts_code": "000001.SZ", "step": 2}]))
            mart.write("limit_cpt_list", {"trade_date": "20260623"}, pd.DataFrame([{"name": "机器人", "limit_num": 5}]))
            mart.write("kpl_list", {"trade_date": "20260623"}, pd.DataFrame([{"ts_code": "000001.SZ", "name": "平安银行"}]))
            mart.write("limit_list_ths", {"trade_date": "20260623"}, pd.DataFrame([{"ts_code": "000001.SZ", "name": "平安银行"}]))
            mart.write("index_classify", {"snapshot_date": "20260623"}, pd.DataFrame([{"index_code": "801780.SI", "industry_name": "银行"}]))
            mart.write(
                "index_member_all",
                {"snapshot_date": "20260623"},
                pd.DataFrame([{"l3_code": "801780.SI", "ts_code": "000001.SZ", "name": "平安银行"}]),
            )
            mart.write(
                "ths_member",
                {"snapshot_date": "20260623"},
                pd.DataFrame([{"_driver_ts_code": "885800.TI", "_driver_name": "机器人", "con_code": "000001.SZ", "con_name": "平安银行"}]),
            )
            mart.write("dc_index", {"trade_date": "20260623"}, pd.DataFrame([{"ts_code": "BK0428.DC", "name": "人形机器人", "idx_type": "概念板块"}]))
            mart.write(
                "dc_member",
                {"trade_date": "20260623"},
                pd.DataFrame([{"_driver_ts_code": "BK0428.DC", "_driver_name": "人形机器人", "con_code": "000001.SZ"}]),
            )
            mart.write(
                "index_weight",
                {"snapshot_date": "20260623"},
                pd.DataFrame([{"index_code": "000300.SH", "con_code": "000001.SZ", "trade_date": "20260623", "weight": 0.5}]),
            )
            mart.write(
                "a_stock_notice",
                {"publish_date": "2026-06-23"},
                pd.DataFrame([{"id": "notice-1", "publish_date": "2026-06-23", "title": "公告"}]),
            )
            mart.write(
                "earnings_forecast",
                {"publish_date": "2026-06-23"},
                pd.DataFrame([{"stock_code": "000001", "publish_date": "2026-06-23", "period": "20260630"}]),
            )
            mart.write(
                "event_news",
                {"news_date": "2026-06-23"},
                pd.DataFrame([{"id": "news-1", "date": "2026-06-23", "title": "新闻"}]),
            )
            mart.write(
                "income",
                {"period": "20260331"},
                pd.DataFrame([{"ts_code": "000001.SZ", "period": "20260331", "revenue": 100.0}]),
            )

            active = [
                "stock_basic",
                "daily",
                "daily_basic",
                "adj_factor",
                "stk_limit",
                "index_daily",
                "index_dailybasic",
                "sw_daily",
                "ci_daily",
                "moneyflow",
                "moneyflow_dc",
                "moneyflow_ths",
                "moneyflow_ind_ths",
                "moneyflow_ind_dc",
                "moneyflow_cnt_ths",
                "top_list",
                "margin_detail",
                "limit_list_d",
                "limit_step",
                "limit_cpt_list",
                "kpl_list",
                "limit_list_ths",
                "index_classify",
                "index_member_all",
                "ths_member",
                "dc_index",
                "dc_member",
                "index_weight",
                "a_stock_notice",
                "earnings_forecast",
                "event_news",
                "income",
            ]
            bundle = build_market_analysis_bundle("2026-06-23", trade_days=2, data_dir=tmp_dir, plan=make_plan(active), event_days=1)

        self.assertEqual(bundle["window"]["start_trade_date"], "20260622")
        self.assertEqual(bundle["window"]["end_trade_date"], "20260623")
        self.assertEqual(bundle["datasets"]["stock_basic"]["rows"], 1)
        self.assertEqual(bundle["datasets"]["daily"]["rows"], 2)
        self.assertEqual(bundle["features"]["price_volume"]["summary"]["stocks"], 1)
        self.assertEqual(bundle["features"]["identity"]["summary"]["industry_count"], 1)
        self.assertIn("moneyflow_dc", bundle["features"]["moneyflow"]["stock_top_by_source"])
        self.assertIn("moneyflow_ind_dc", bundle["features"]["moneyflow"]["industry_sample_by_source"])
        self.assertEqual(bundle["features"]["limit_pool"]["stats"]["limit_up"], 1)
        self.assertEqual(bundle["features"]["limit_pool"]["stats"]["limit_down"], 1)
        self.assertEqual(bundle["features"]["limit_pool"]["stats"]["broken_limit"], 1)
        self.assertEqual(bundle["datasets"]["top_list"]["rows"], 1)
        self.assertEqual(bundle["datasets"]["a_stock_notice"]["rows"], 1)
        self.assertEqual(bundle["datasets"]["earnings_forecast"]["rows"], 1)
        self.assertEqual(bundle["datasets"]["event_news"]["rows"], 1)
        self.assertEqual(bundle["datasets"]["income"]["rows"], 1)
        self.assertEqual(bundle["datasets"]["index_member_all"]["rows"], 1)
        self.assertEqual(bundle["features"]["membership"]["summary"]["dc_member"]["rows"], 1)
        self.assertEqual(bundle["features"]["membership"]["member_samples"]["ths_member"][0]["_driver_name"], "机器人")
        self.assertEqual(bundle["features"]["financials"]["income_sample"][0]["period"], "20260331")
        self.assertEqual(bundle["coverage"]["earnings_forecast"]["missing_count"], 0)
        self.assertFalse(bundle["provenance"]["event_news"]["historical_backfill"])
        self.assertEqual(bundle["data_gaps"], [])

    def test_market_bundle_only_reads_active_plan_datasets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mart = MartStore(data_dir=tmp_dir)
            mart.write(
                "trade_cal",
                {"exchange": "SSE"},
                pd.DataFrame([{"cal_date": "20260623", "is_open": 1}]),
            )
            mart.write(
                "daily",
                {"trade_date": "20260623"},
                pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260623", "close": 10.0}]),
            )
            mart.write(
                "moneyflow",
                {"trade_date": "20260623"},
                pd.DataFrame([{"ts_code": "000001.SZ", "net_mf_amount": 5.0}]),
            )

            bundle = build_market_analysis_bundle("2026-06-23", trade_days=1, data_dir=tmp_dir, plan=make_plan(["daily"]), event_days=1)

        self.assertIn("daily", bundle["datasets"])
        self.assertNotIn("moneyflow", bundle["datasets"])
        self.assertEqual(bundle["features"]["moneyflow"]["stock_top_by_source"], {})


if __name__ == "__main__":
    unittest.main()
