import json
import tempfile
import unittest
from pathlib import Path

from ashare_data_provider.news import TushareNewsError
from ashare_data_provider.news_store import (
    merge_news_date_partitions,
    news_date_partition_path,
    news_record_date,
    partition_news_records_by_date,
    read_news_date_partitions,
)


class NewsStoreTest(unittest.TestCase):
    def test_news_record_date_prefers_record_date(self) -> None:
        record = {"date": "20260601", "datetime": "2026-06-02 09:31:00"}

        self.assertEqual(news_record_date(record), "2026-06-01")

    def test_news_record_date_falls_back_to_datetime(self) -> None:
        record = {"datetime": "2026-06-02 09:31:00"}

        self.assertEqual(news_record_date(record), "2026-06-02")

    def test_news_record_date_requires_real_date(self) -> None:
        with self.assertRaisesRegex(TushareNewsError, "缺少 date/datetime"):
            news_record_date({"content": "a"})

        with self.assertRaisesRegex(TushareNewsError, "无法识别时讯日期"):
            news_record_date({"date": "2026-99-99"})

    def test_partition_news_records_by_date(self) -> None:
        records = [
            {"dedupe_key": "k1", "date": "2026-06-01"},
            {"dedupe_key": "k2", "datetime": "2026-06-02 09:31:00"},
            {"dedupe_key": "k3", "date": "2026-06-01"},
        ]

        partitions = partition_news_records_by_date(records)

        self.assertEqual([record["dedupe_key"] for record in partitions["2026-06-01"]], ["k1", "k3"])
        self.assertEqual([record["dedupe_key"] for record in partitions["2026-06-02"]], ["k2"])

    def test_merge_news_date_partitions_writes_and_updates_only_touched_dates(self) -> None:
        first_records = [
            {"dedupe_key": "k1", "date": "2026-06-01", "datetime": "2026-06-01 09:31:00", "fetched_at": "2026-06-01T09:40:00+08:00"},
            {"dedupe_key": "k2", "date": "2026-06-02", "datetime": "2026-06-02 09:31:00", "fetched_at": "2026-06-02T09:40:00+08:00"},
        ]
        second_records = [
            {"dedupe_key": "k1", "date": "2026-06-01", "datetime": "2026-06-01 09:31:00", "fetched_at": "2026-06-03T09:40:00+08:00"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)

            first_paths = merge_news_date_partitions(base_dir, first_records, snapshot_file="snapshot-a.jsonl")
            second_paths = merge_news_date_partitions(base_dir, second_records, snapshot_file="snapshot-b.jsonl")

            self.assertEqual(sorted(path.name for path in first_paths), ["2026-06-01.jsonl", "2026-06-02.jsonl"])
            self.assertEqual([path.name for path in second_paths], ["2026-06-01.jsonl"])
            first_day = [json.loads(line) for line in news_date_partition_path(base_dir, "2026-06-01").read_text(encoding="utf-8").splitlines()]
            second_day = [json.loads(line) for line in news_date_partition_path(base_dir, "2026-06-02").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(first_day), 1)
            self.assertEqual(first_day[0]["seen_count"], 2)
            self.assertEqual(first_day[0]["last_seen_at"], "2026-06-03T09:40:00+08:00")
            self.assertEqual(first_day[0]["snapshot_files"], ["snapshot-a.jsonl", "snapshot-b.jsonl"])
            self.assertEqual([record["dedupe_key"] for record in second_day], ["k2"])

    def test_read_news_date_partitions_reads_only_requested_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            merge_news_date_partitions(
                base_dir,
                [
                    {"dedupe_key": "k1", "date": "2026-06-01", "datetime": "2026-06-01 09:31:00"},
                    {"dedupe_key": "k2", "date": "2026-06-02", "datetime": "2026-06-02 09:31:00"},
                ],
            )

            records = read_news_date_partitions(base_dir, ["2026-06-02", "2026-06-03"])

            self.assertEqual([record["dedupe_key"] for record in records], ["k2"])


if __name__ == "__main__":
    unittest.main()
