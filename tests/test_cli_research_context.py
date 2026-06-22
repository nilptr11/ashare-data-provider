import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ashare_data_provider.cli import main


class CliResearchContextTest(unittest.TestCase):
    def test_research_context_outputs_json(self) -> None:
        payload = {"schema": "ashare.research_context.v1", "ts_code": "000001.SZ"}
        with patch("ashare_data_provider.cli.AShareProvider") as provider_cls:
            with patch("ashare_data_provider.cli.build_research_context", return_value=payload) as build:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = main(
                        [
                            "research-context",
                            "000001.SZ",
                            "--as-of",
                            "2026-06-02",
                            "--profile",
                            "standard",
                            "--lookback-days",
                            "30",
                            "--financial-years",
                            "2",
                            "--event-days",
                            "45",
                            "--forecast-days",
                            "90",
                            "--include-news",
                            "--news-source",
                            "cls",
                            "--max-rows-per-dataset",
                            "10",
                            "--current-points",
                            "5000",
                        ]
                    )

        self.assertEqual(code, 0)
        provider_cls.assert_called_once()
        build.assert_called_once()
        kwargs = build.call_args.kwargs
        self.assertEqual(kwargs["ts_code"], "000001.SZ")
        self.assertEqual(kwargs["as_of"], "2026-06-02")
        self.assertEqual(kwargs["profile"], "standard")
        self.assertEqual(kwargs["lookback_days"], 30)
        self.assertEqual(kwargs["financial_years"], 2)
        self.assertEqual(kwargs["event_days"], 45)
        self.assertEqual(kwargs["forecast_days"], 90)
        self.assertTrue(kwargs["include_news"])
        self.assertEqual(kwargs["news_sources"], ["cls"])
        self.assertEqual(kwargs["max_rows_per_dataset"], 10)
        self.assertEqual(json.loads(buffer.getvalue())["schema"], "ashare.research_context.v1")

    def test_research_summary_outputs_json_from_context_file(self) -> None:
        payload = {
            "schema": "ashare.research_context.v1",
            "ts_code": "000001.SZ",
            "symbol": "000001",
            "target": {"stock_basic": {"name": "平安银行", "industry": "银行"}},
            "calendar": {"completed_trade_date": "20260601"},
            "market": {"daily_latest": {"records": [{"trade_date": "20260601", "close": 10.5}]}},
            "fundamentals": {},
            "events": {},
            "data_gaps": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "context.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(["research-summary", str(path), "--format", "json"])

        self.assertEqual(code, 0)
        summary = json.loads(buffer.getvalue())
        self.assertEqual(summary["schema"], "ashare.research_summary.v1")
        self.assertEqual(summary["target"]["name"], "平安银行")


if __name__ == "__main__":
    unittest.main()
