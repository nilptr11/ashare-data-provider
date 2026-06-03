import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tushare_fastcli.cli import main


class CliEventsTest(unittest.TestCase):
    def test_events_notice_outputs_provider_records(self) -> None:
        with patch("tushare_fastcli.provider.TushareProvider.a_stock_notice", return_value=[{"event_type": "notice", "title": "公告"}]) as notice:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(["events", "notice", "--days", "3", "--end-date", "20260603", "--format", "json"])

        self.assertEqual(code, 0)
        notice.assert_called_once()
        self.assertIn('"event_type": "notice"', buffer.getvalue())

    def test_events_forecast_outputs_provider_records(self) -> None:
        with patch("tushare_fastcli.provider.TushareProvider.earnings_forecast", return_value=[{"event_type": "forecast", "period": "20260331"}]) as forecast:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(["events", "forecast", "--period", "20260331", "--format", "json"])

        self.assertEqual(code, 0)
        forecast.assert_called_once()
        self.assertIn('"event_type": "forecast"', buffer.getvalue())

    def test_events_news_reuses_page_crawler(self) -> None:
        payload = {"sources": [], "records": [{"src": "cls", "content": "a"}]}
        with patch("tushare_fastcli.cli.load_tushare_cookie", return_value="uid=1; username=u"):
            with patch("tushare_fastcli.cli.crawl_tushare_news", return_value=payload) as crawl:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = main(["events", "news", "--source", "cls", "--format", "json"])

        self.assertEqual(code, 0)
        crawl.assert_called_once()
        self.assertIn('"src": "cls"', buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
