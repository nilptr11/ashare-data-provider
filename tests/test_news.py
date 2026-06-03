import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tushare_fastcli.news import (
    TushareNewsError,
    TushareNewsParseError,
    build_news_records,
    merge_news_records,
    load_tushare_cookie,
    normalize_news_sources,
    parse_news_page,
)
from tushare_fastcli.output import render


NEWS_HTML = """
<html>
  <head>
    <title>Tushare数据</title>
    <meta name="description" content="财经数据">
  </head>
  <body>
    <div id="navigation"><a href="/news/sina">资讯数据</a></div>
    <div id="data_source_head">
      <span class="source_name"><a href="/news/sina">新浪财经</a></span>
      <span class="source_name cur"><a href="/news/cls">财联社</a></span>
    </div>
    <div id="channel_head">
      <span class="channel_name">快讯</span>
      <span class="channel_name">7*24</span>
    </div>
    <div id="news_快讯">
      <div class="news_item">
        <span class="news_datetime">09:31</span>
        <span class="news_content">【AI算力】产业链订单继续增长</span>
      </div>
    </div>
    <div id="news_7*24">
      <div class="news_item">
        <span class="news_datetime">09:32</span>
        <span class="news_content">公司互动|多家公司披露机器人业务进展</span>
      </div>
    </div>
    <input id="search-input" />
    <button id="search-button"></button>
  </body>
</html>
"""


class NewsTest(unittest.TestCase):
    def test_parse_news_page_extracts_sources_channels_and_items(self) -> None:
        page = parse_news_page(NEWS_HTML, "cls")

        self.assertEqual(page["current_source"], "财联社")
        self.assertEqual(page["total_items"], 2)
        self.assertEqual(page["channels"][0]["name"], "快讯")
        self.assertEqual(page["channels"][1]["name"], "7*24")
        self.assertEqual(page["channels"][0]["items"][0]["content"], "【AI算力】产业链订单继续增长")
        self.assertTrue(page["has_search"])

    def test_parse_news_page_uses_anchor_date_and_day_markers(self) -> None:
        html = NEWS_HTML.replace(
            '<div id="news_快讯">\n      <div class="news_item">',
            '<div id="news_快讯">\n      <div class="news_item">',
        ).replace(
            '    </div>\n    <div id="news_7*24">',
            '      <div class="news_day news_item">5月31日</div>\n      <div class="news_item"><span class="news_datetime">23:59</span><span class="news_content">昨日消息</span></div>\n    </div>\n    <div id="news_7*24">',
        )

        page = parse_news_page(html, "cls", anchor_date="2026-06-01")
        records = build_news_records([page], fetched_at="2026-06-01T09:40:00+08:00")

        self.assertEqual(records[0]["date"], "2026-06-01")
        self.assertEqual(records[0]["datetime"], "2026-06-01 09:31:00")
        self.assertEqual(records[0]["date_source"], "anchor_date")
        self.assertEqual(records[1]["date"], "2026-05-31")
        self.assertEqual(records[1]["datetime"], "2026-05-31 23:59:00")
        self.assertEqual(records[1]["date_source"], "page_day_marker")

    def test_build_news_records_outputs_db_friendly_rows(self) -> None:
        page = parse_news_page(NEWS_HTML, "cls")
        records = build_news_records([page], fetched_at="2026-06-01T09:40:00+08:00", publish_date="2026-06-01")

        self.assertEqual(records[0]["src"], "cls")
        self.assertEqual(records[0]["source_name"], "财联社")
        self.assertEqual(records[0]["channel"], "快讯")
        self.assertEqual(records[0]["date"], "2026-06-01")
        self.assertEqual(records[0]["datetime"], "2026-06-01 09:31:00")
        self.assertEqual(records[0]["date_source"], "explicit_publish_date")
        self.assertEqual(records[0]["date_confidence"], "high")
        self.assertEqual(records[0]["title"], "AI算力")
        self.assertEqual(records[0]["body"], "产业链订单继续增长")
        self.assertEqual(len(records[0]["id"]), 64)
        self.assertEqual(len(records[0]["content_hash"]), 64)
        self.assertEqual(len(records[0]["dedupe_key"]), 64)
        self.assertEqual(records[1]["title"], "公司互动")
        self.assertEqual(records[1]["body"], "多家公司披露机器人业务进展")

    def test_content_hash_ignores_title_delimiter_style(self) -> None:
        bracket_page = parse_news_page(NEWS_HTML, "cls")
        pipe_page = parse_news_page(
            NEWS_HTML.replace("【AI算力】产业链订单继续增长", "AI算力|产业链订单继续增长"),
            "cls",
        )

        bracket_record = build_news_records([bracket_page])[0]
        pipe_record = build_news_records([pipe_page])[0]

        self.assertEqual(bracket_record["content_hash"], pipe_record["content_hash"])
        self.assertNotEqual(bracket_record["id"], pipe_record["id"])

    def test_merge_news_records_dedupes_and_tracks_seen_metadata(self) -> None:
        first = {
            "dedupe_key": "k1",
            "datetime": "2026-06-01 09:31:00",
            "fetched_at": "2026-06-01T09:40:00+08:00",
            "sequence": 1,
            "content": "a",
        }
        second = dict(first, fetched_at="2026-06-02T09:40:00+08:00")
        newer = {
            "dedupe_key": "k2",
            "datetime": "2026-06-02 09:31:00",
            "fetched_at": "2026-06-02T09:40:00+08:00",
            "sequence": 1,
            "content": "b",
        }

        merged = merge_news_records([[first], [second, newer]], snapshot_files=["a.jsonl", "b.jsonl"])

        self.assertEqual([record["dedupe_key"] for record in merged], ["k2", "k1"])
        self.assertEqual(merged[1]["seen_count"], 2)
        self.assertEqual(merged[1]["first_seen_at"], "2026-06-01T09:40:00+08:00")
        self.assertEqual(merged[1]["last_seen_at"], "2026-06-02T09:40:00+08:00")
        self.assertEqual(merged[1]["snapshot_files"], ["a.jsonl", "b.jsonl"])

    def test_record_csv_render_supports_flattened_news_rows(self) -> None:
        csv_text = render([{"src": "sina", "content": "a"}, {"src": "cls", "content": "b"}], "csv")

        self.assertEqual(csv_text.splitlines()[0], "src,content")
        self.assertIn("sina,a", csv_text)

    def test_record_table_render_supports_flattened_records(self) -> None:
        table = render([{"src": "sina", "content": "a"}, {"src": "cls", "content": "b"}], "table")

        self.assertIn("src", table.splitlines()[0])
        self.assertIn("content", table.splitlines()[0])
        self.assertIn("sina", table)
        self.assertIn("cls", table)

    def test_record_table_render_shortens_nested_raw(self) -> None:
        table = render([{"event_type": "notice", "raw": {"公告标题": "很长的公告"}}], "table")

        self.assertIn("event_type", table)
        self.assertIn("raw", table)
        self.assertIn("公告标题", table)

    def test_parse_news_page_rejects_login_shell(self) -> None:
        with self.assertRaisesRegex(TushareNewsParseError, "登录页或前端壳页"):
            parse_news_page('<a href="/weborder/#/login">login</a>', "sina")

    def test_normalize_news_sources_dedupes_and_rejects_unknown(self) -> None:
        self.assertEqual(normalize_news_sources(["sina", "cls", "sina"]), ["sina", "cls"])

        with self.assertRaisesRegex(TushareNewsError, "未知资讯来源"):
            normalize_news_sources(["unknown"])

    def test_load_cookie_prefers_explicit_cookie_and_reads_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text("TUSHARE_COOKIE='uid=1; username=file_user'\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_tushare_cookie(env_file=env_path), "uid=1; username=file_user")
                self.assertEqual(
                    load_tushare_cookie(cookie="uid=2; username=arg_user", env_file=env_path),
                    "uid=2; username=arg_user",
                )


if __name__ == "__main__":
    unittest.main()
