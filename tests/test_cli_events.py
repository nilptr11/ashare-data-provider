import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ashare_data_provider.cli import main


class CliEventsTest(unittest.TestCase):
    def test_events_notice_outputs_provider_records(self) -> None:
        with patch("ashare_data_provider.provider.AShareProvider.a_stock_notice", return_value=[{"event_type": "notice", "title": "公告"}]) as notice:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(["events", "notice", "--days", "3", "--end-date", "20260603", "--format", "json"])

        self.assertEqual(code, 0)
        notice.assert_called_once()
        self.assertIn('"event_type": "notice"', buffer.getvalue())

    def test_events_forecast_outputs_provider_records(self) -> None:
        with patch("ashare_data_provider.provider.AShareProvider.earnings_forecast", return_value=[{"event_type": "forecast", "period": "20260331"}]) as forecast:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(["events", "forecast", "--period", "20260331", "--format", "json"])

        self.assertEqual(code, 0)
        forecast.assert_called_once()
        self.assertIn('"event_type": "forecast"', buffer.getvalue())

    def test_events_news_reuses_page_crawler(self) -> None:
        payload = {"sources": [], "records": [{"src": "cls", "content": "a"}]}
        with patch("ashare_data_provider.cli.load_tushare_cookie", return_value="uid=1; username=u"):
            with patch("ashare_data_provider.cli.crawl_tushare_news", return_value=payload) as crawl:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = main(["events", "news", "--source", "cls", "--format", "json"])

        self.assertEqual(code, 0)
        crawl.assert_called_once()
        self.assertIn('"src": "cls"', buffer.getvalue())

    def test_top_level_news_command_is_removed(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                main(["news", "--source", "cls"])

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())

    def test_cache_command_is_not_public(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                main(["cache", "call", "daily"])

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())

    def test_events_news_writes_snapshot_and_merged_output(self) -> None:
        payload = {"sources": [], "records": [{"dedupe_key": "k1", "datetime": "2026-06-03 09:31:00", "fetched_at": "2026-06-03T09:40:00+08:00", "src": "cls"}]}
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot = Path(tmp_dir) / "snapshot.jsonl"
            merged = Path(tmp_dir) / "combined.jsonl"
            with patch("ashare_data_provider.cli.load_tushare_cookie", return_value="uid=1; username=u"):
                with patch("ashare_data_provider.cli.crawl_tushare_news", return_value=payload):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        code = main([
                            "events",
                            "news",
                            "--source",
                            "cls",
                            "--format",
                            "jsonl",
                            "--snapshot-output",
                            str(snapshot),
                            "--merge-output",
                            str(merged),
                        ])

            self.assertEqual(code, 0)
            self.assertTrue(snapshot.exists())
            self.assertTrue(merged.exists())
            rows = [json.loads(line) for line in merged.read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(rows[0]["dedupe_key"], "k1")
            self.assertEqual(rows[0]["seen_count"], 1)

    def test_events_news_merge_command_dedupes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            first = Path(tmp_dir) / "a.jsonl"
            second = Path(tmp_dir) / "b.jsonl"
            output = Path(tmp_dir) / "combined.json"
            first.write_text(json.dumps({"dedupe_key": "k1", "datetime": "2026-06-01 09:31:00", "fetched_at": "2026-06-01T09:40:00+08:00"}, ensure_ascii=False) + "\n", encoding="utf-8")
            second.write_text(json.dumps({"dedupe_key": "k1", "datetime": "2026-06-01 09:31:00", "fetched_at": "2026-06-02T09:40:00+08:00"}, ensure_ascii=False) + "\n", encoding="utf-8")

            code = main(["events", "news-merge", "--input", str(first), "--input", str(second), "--format", "json", "--output", str(output)])

            self.assertEqual(code, 0)
            rows = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["seen_count"], 2)


if __name__ == "__main__":
    unittest.main()
