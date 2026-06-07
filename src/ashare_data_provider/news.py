from __future__ import annotations

import hashlib
import os
import re
import csv
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .config import read_env_file


BASE_URL = "https://tushare.pro"
DEFAULT_NEWS_SOURCES = [
    "xq",
    "jinshi",
    "jinrongjie",
    "10jqka",
    "yicai",
    "cls",
    "eastmoney",
    "wallstreetcn",
    "sina",
]


class TushareNewsError(RuntimeError):
    pass


class TushareNewsFetchError(TushareNewsError):
    pass


class TushareNewsParseError(TushareNewsError):
    pass


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _text_of(node: Any) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def normalize_news_sources(sources: list[str] | tuple[str, ...] | None = None) -> list[str]:
    resolved = list(sources or DEFAULT_NEWS_SOURCES)
    unknown = sorted(set(resolved) - set(DEFAULT_NEWS_SOURCES))
    if unknown:
        raise TushareNewsError(f"未知资讯来源 slug：{', '.join(unknown)}；可选：{', '.join(DEFAULT_NEWS_SOURCES)}")

    deduped: list[str] = []
    for source in resolved:
        if source not in deduped:
            deduped.append(source)
    return deduped


def load_tushare_cookie(
    cookie: str | None = None,
    cookie_file: str | Path | None = None,
    cookie_env: str = "TUSHARE_COOKIE",
    env_file: str | Path = ".env",
    validate: bool = True,
) -> str:
    if _normalize(cookie) is not None:
        resolved = str(cookie).strip()
    elif cookie_file:
        resolved = Path(cookie_file).read_text(encoding="utf-8").strip()
    else:
        env_values = read_env_file(env_file)
        resolved = _normalize(os.getenv(cookie_env)) or _normalize(env_values.get(cookie_env)) or ""

    if not resolved:
        raise TushareNewsError(f"缺少 Tushare 登录 Cookie，请设置 {cookie_env} 或传入 --cookie-file/--cookie")
    if validate:
        validate_tushare_cookie(resolved)
    return resolved


def validate_tushare_cookie(cookie: str) -> None:
    if not re.search(r"(^|;\s*)uid=", cookie) or not re.search(r"(^|;\s*)username=", cookie):
        raise TushareNewsError("Cookie 中未发现 uid/username，可能不是 Tushare 登录 Cookie")


def _request_headers(cookie: str) -> dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": f"{BASE_URL}/news/sina",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
    }


def fetch_news_html(cookie: str, slug: str, timeout: float = 30.0, retries: int = 2) -> str:
    url = f"{BASE_URL}/news/{slug}"
    request = Request(url, headers=_request_headers(cookie))
    attempts = max(retries, 0) + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except HTTPError as exc:
            raise TushareNewsFetchError(f"{slug}: HTTP {exc.code} {exc.reason}") from exc
        except TimeoutError as exc:
            last_error = exc
            message = "请求超时"
        except URLError as exc:
            last_error = exc
            message = f"请求失败：{exc.reason}"
        except OSError as exc:
            last_error = exc
            message = f"请求失败：{exc}"

        if attempt < attempts:
            time.sleep(min(0.5 * attempt, 2.0))

    raise TushareNewsFetchError(f"{slug}: {message}") from last_error


def _make_soup(html: str) -> Any:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise TushareNewsParseError("解析 Tushare 资讯页需要 beautifulsoup4，请先安装项目依赖") from exc
    return BeautifulSoup(html, "html.parser")


def _parse_date(value: str | date | datetime | None) -> date:
    if value is None:
        return _now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return datetime.strptime(text, "%Y-%m-%d").date()
        if re.fullmatch(r"\d{8}", text):
            return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise TushareNewsError(f"日期必须是 YYYY-MM-DD 或 YYYYMMDD：{value}") from exc
    raise TushareNewsError(f"日期必须是 YYYY-MM-DD 或 YYYYMMDD：{value}")


def _parse_news_day_marker(text: str, anchor_date: date) -> date | None:
    match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not match:
        return None
    month = int(match.group(1))
    day = int(match.group(2))
    try:
        resolved = date(anchor_date.year, month, day)
    except ValueError:
        return None
    if resolved > anchor_date:
        resolved = date(anchor_date.year - 1, month, day)
    return resolved


def _combine_date_time(item_date: date | None, publish_time: str) -> str | None:
    if item_date is None or not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", publish_time):
        return None
    time_text = publish_time if publish_time.count(":") == 2 else f"{publish_time}:00"
    return f"{item_date.isoformat()} {time_text}"


def _has_class(node: Any, class_name: str) -> bool:
    return class_name in (node.get("class") or [])


def parse_news_page(html: str, slug: str, anchor_date: str | date | datetime | None = None) -> dict[str, Any]:
    soup = _make_soup(html)
    resolved_anchor_date = _parse_date(anchor_date)
    meta: dict[str, str] = {}
    for item in soup.select("meta"):
        name = item.get("name") or item.get("property") or item.get("http-equiv")
        content = item.get("content")
        if name and content:
            meta[str(name)] = str(content)

    navigation = [
        {"text": _text_of(link), "href": urljoin(BASE_URL, link.get("href", ""))}
        for link in soup.select("#navigation a")
        if _text_of(link)
    ]

    data_sources = []
    for span in soup.select("#data_source_head span.source_name"):
        link = span.find("a")
        if not link:
            continue
        href = str(link.get("href", ""))
        data_sources.append(
            {
                "name": _text_of(link),
                "slug": href.rstrip("/").split("/")[-1],
                "href": urljoin(BASE_URL, href),
                "current": "cur" in (span.get("class") or []),
            }
        )

    channel_names = [_text_of(channel) for channel in soup.select("#channel_head .channel_name")]
    if not channel_names:
        channel_names = [
            str(container.get("id"))[len("news_") :]
            for container in soup.find_all(id=re.compile(r"^news_"))
            if str(container.get("id", "")).startswith("news_")
        ]

    channels = []
    for channel_name in channel_names:
        container = soup.find(id=f"news_{channel_name}")
        items = []
        current_date = resolved_anchor_date
        current_date_source = "anchor_date"
        if container:
            for child in container.children:
                if not getattr(child, "get", None) or not _has_class(child, "news_item"):
                    continue
                if _has_class(child, "news_day"):
                    marker_date = _parse_news_day_marker(_text_of(child), resolved_anchor_date)
                    if marker_date is not None:
                        current_date = marker_date
                        current_date_source = "page_day_marker"
                    continue

                content = _text_of(child.select_one(".news_content"))
                if not content:
                    content = _text_of(child)
                if not content:
                    continue
                publish_time = _text_of(child.select_one(".news_datetime"))
                items.append(
                    {
                        "date": current_date.isoformat(),
                        "time": publish_time,
                        "datetime": _combine_date_time(current_date, publish_time),
                        "date_source": current_date_source,
                        "content": content,
                        "position": len(items) + 1,
                    }
                )
        channels.append({"name": channel_name, "count": len(items), "items": items})

    current_source = next((source["name"] for source in data_sources if source["current"]), "")
    if not current_source:
        current_source = next((source["name"] for source in data_sources if source["slug"] == slug), "")

    parsed = {
        "slug": slug,
        "url": f"{BASE_URL}/news/{slug}",
        "title": _text_of(soup.title),
        "meta": meta,
        "navigation": navigation,
        "data_sources": data_sources,
        "current_source": current_source,
        "anchor_date": resolved_anchor_date.isoformat(),
        "channels": channels,
        "total_items": sum(channel["count"] for channel in channels),
        "has_search": soup.select_one("#search-input") is not None and soup.select_one("#search-button") is not None,
    }
    validate_news_page(parsed, html)
    return parsed


def validate_news_page(parsed: dict[str, Any], html: str) -> None:
    if "/weborder/#/login" in html or "We're sorry but Tushare数据 doesn't work properly" in html:
        raise TushareNewsParseError("返回的是登录页或前端壳页，请更新 TUSHARE_COOKIE 后重试")
    if not parsed["data_sources"] or not parsed["current_source"]:
        raise TushareNewsParseError("未解析到资讯来源导航，可能是 Cookie 失效或页面结构变化")
    if not parsed["channels"]:
        raise TushareNewsParseError("未解析到频道列表，可能是页面结构变化")


def _extract_title_body(content: str) -> tuple[str, str]:
    bracket_match = re.match(r"^【([^】]{1,120})】\s*(.*)$", content)
    if bracket_match:
        return bracket_match.group(1).strip(), bracket_match.group(2).strip()

    if "|" in content:
        title, body = content.split("|", 1)
        title = title.strip()
        body = body.strip()
        if 0 < len(title) <= 120 and body:
            return title, body
    return "", content


def _normalize_hash_text(value: str) -> str:
    return " ".join(value.strip().split())


def _hash_parts(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _content_hash(title: str, body: str, content: str) -> str:
    if title or body:
        return _hash_parts(_normalize_hash_text(title), _normalize_hash_text(body))
    return _hash_parts(_normalize_hash_text(content))


def _explicit_date_fields(publish_date: str | None, publish_time: str) -> tuple[str | None, str | None, str | None]:
    if publish_date is None:
        return None, None, None
    explicit_date = _parse_date(publish_date)
    return (
        explicit_date.isoformat(),
        _combine_date_time(explicit_date, publish_time),
        "explicit_publish_date",
    )


def build_news_records(
    pages: list[dict[str, Any]],
    fetched_at: str | None = None,
    publish_date: str | None = None,
) -> list[dict[str, Any]]:
    resolved_fetched_at = fetched_at or _now_iso()
    records: list[dict[str, Any]] = []
    for page in pages:
        source_name = page.get("current_source", "")
        for channel in page.get("channels", []):
            for item in channel.get("items", []):
                content = str(item.get("content", "")).strip()
                title, body = _extract_title_body(content)
                publish_time = str(item.get("time", "")).strip()
                content_hash = _content_hash(title, body, content)
                explicit_date, explicit_datetime, explicit_source = _explicit_date_fields(publish_date, publish_time)
                item_date = explicit_date or item.get("date")
                item_datetime = explicit_datetime or item.get("datetime")
                date_source = explicit_source or item.get("date_source")
                record = {
                    "id": _hash_parts(page["slug"], channel["name"], item_datetime or publish_time, content),
                    "content_hash": content_hash,
                    "dedupe_key": _hash_parts("tushare_news_page", page["slug"], channel["name"], item_datetime or publish_time, content_hash),
                    "src": page["slug"],
                    "source": source_name,
                    "source_name": source_name,
                    "source_url": page["url"],
                    "channel": channel["name"],
                    "date": item_date,
                    "time": publish_time,
                    "datetime": item_datetime,
                    "date_source": date_source,
                    "title": title,
                    "content": content,
                    "body": body,
                    "sequence": len(records) + 1,
                    "channel_sequence": item.get("position"),
                    "fetched_at": resolved_fetched_at,
                    "source_kind": "tushare_news_page",
                }
                records.append(record)
    return records


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
    return records


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_news_records(path: str | Path) -> list[dict[str, Any]]:
    resolved = Path(path)
    if resolved.suffix == ".jsonl":
        return _read_jsonl(resolved)
    if resolved.suffix == ".csv":
        return _read_csv(resolved)
    data = json.loads(resolved.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return [item for item in data["records"] if isinstance(item, dict)]
    raise TushareNewsError(f"无法从文件读取时讯 records：{resolved}")


def _news_merge_key(record: dict[str, Any]) -> str:
    return str(
        record.get("dedupe_key")
        or _hash_parts(
            str(record.get("source_kind", "tushare_news_page")),
            str(record.get("src", "")),
            str(record.get("channel", "")),
            str(record.get("datetime") or record.get("time") or ""),
            str(record.get("content_hash") or record.get("content") or ""),
        )
    )


def _sort_news_record(record: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(record.get("datetime") or record.get("date") or ""),
        str(record.get("fetched_at") or ""),
        -int(record.get("sequence") or 0),
    )


def _seen_count(record: dict[str, Any]) -> int:
    try:
        return max(int(record.get("seen_count") or 1), 1)
    except (TypeError, ValueError):
        return 1


def _seen_times(record: dict[str, Any]) -> list[str]:
    return [
        str(value)
        for value in [record.get("first_seen_at"), record.get("last_seen_at"), record.get("fetched_at")]
        if value
    ]


def _snapshot_files(record: dict[str, Any], snapshot_file: str | None = None) -> list[str]:
    files = []
    existing = record.get("snapshot_files")
    if isinstance(existing, list):
        files.extend(str(value) for value in existing if value)
    if snapshot_file and snapshot_file not in files:
        files.append(snapshot_file)
    return files


def merge_news_records(record_groups: list[list[dict[str, Any]]], snapshot_files: list[str | None] | None = None) -> list[dict[str, Any]]:
    files = snapshot_files or []
    merged: dict[str, dict[str, Any]] = {}
    for group_index, records in enumerate(record_groups):
        snapshot_file = files[group_index] if group_index < len(files) else None
        for record in records:
            key = _news_merge_key(record)
            if key not in merged:
                merged[key] = dict(record)
                times = _seen_times(record)
                merged[key]["first_seen_at"] = min(times) if times else None
                merged[key]["last_seen_at"] = max(times) if times else None
                merged[key]["seen_count"] = _seen_count(record)
                merged[key]["snapshot_files"] = _snapshot_files(record, snapshot_file)
                continue

            existing = merged[key]
            seen_times = _seen_times(existing) + _seen_times(record)
            if seen_times:
                existing["first_seen_at"] = min(seen_times)
                existing["last_seen_at"] = max(seen_times)
            existing["seen_count"] = _seen_count(existing) + _seen_count(record)
            for file in _snapshot_files(record, snapshot_file):
                if file not in existing.get("snapshot_files", []):
                    existing.setdefault("snapshot_files", []).append(file)

    return sorted(merged.values(), key=_sort_news_record, reverse=True)


def merge_news_files(paths: list[str | Path]) -> list[dict[str, Any]]:
    resolved_paths = [Path(path) for path in paths]
    return merge_news_records(
        [read_news_records(path) for path in resolved_paths],
        snapshot_files=[str(path) for path in resolved_paths],
    )


def build_news_summary(pages: list[dict[str, Any]], fetched_at: str) -> dict[str, Any]:
    return {
        "base_url": f"{BASE_URL}/news",
        "fetched_at": fetched_at,
        "anchor_dates": sorted({str(page.get("anchor_date", "")) for page in pages if page.get("anchor_date")}),
        "source_kind": "tushare_news_page",
        "sources": [
            {
                "slug": page["slug"],
                "name": page["current_source"],
                "url": page["url"],
                "channels": [{"name": channel["name"], "count": channel["count"]} for channel in page["channels"]],
                "total_items": page["total_items"],
            }
            for page in pages
        ],
    }


def crawl_tushare_news(
    cookie: str,
    sources: list[str] | tuple[str, ...] | None = None,
    timeout: float = 30.0,
    delay: float = 0.3,
    retries: int = 2,
    publish_date: str | None = None,
    anchor_date: str | date | datetime | None = None,
) -> dict[str, Any]:
    resolved_sources = normalize_news_sources(sources)
    fetched_at = _now_iso()
    resolved_anchor_date = publish_date or anchor_date or fetched_at[:10]
    pages = []
    for index, slug in enumerate(resolved_sources, start=1):
        pages.append(parse_news_page(fetch_news_html(cookie, slug, timeout=timeout, retries=retries), slug, anchor_date=resolved_anchor_date))
        if index < len(resolved_sources) and delay > 0:
            time.sleep(delay)

    payload = build_news_summary(pages, fetched_at)
    payload["records"] = build_news_records(pages, fetched_at=fetched_at, publish_date=publish_date)
    return payload
