from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib import parse, request

from .base import SourceAdapterError


@dataclass(frozen=True)
class HttpResponse:
    status: int
    url: str
    text: str
    headers: dict[str, str]

    def json(self) -> Any:
        try:
            return json.loads(self.text) if self.text else None
        except json.JSONDecodeError as error:
            raise SourceAdapterError(f"response is not valid JSON: {self.url}") from error


@dataclass(frozen=True)
class HttpBinaryResponse:
    status: int
    url: str
    content: bytes
    headers: dict[str, str]


HttpTransport = Callable[[str, dict[str, Any], dict[str, str], int], HttpResponse]
HttpBinaryTransport = Callable[[str, dict[str, Any], dict[str, str], int], HttpBinaryResponse]


def urllib_get_text(url: str, params: dict[str, Any], headers: dict[str, str], timeout: int) -> HttpResponse:
    query = parse.urlencode(params, doseq=True)
    target_url = url
    if query:
        separator = "&" if parse.urlparse(url).query else "?"
        target_url = f"{url}{separator}{query}"
    req = request.Request(target_url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - source URLs are fixed adapter endpoints.
            content = response.read()
            response_headers = {str(key): str(value) for key, value in response.headers.items()}
            return HttpResponse(
                status=int(response.status),
                url=str(response.geturl()),
                text=decode_response_text(content, response_headers),
                headers=response_headers,
            )
    except Exception as error:  # pragma: no cover - wraps network errors.
        raise SourceAdapterError(f"HTTP request failed for {target_url}: {error}") from error


def urllib_get_json(url: str, params: dict[str, Any], headers: dict[str, str], timeout: int) -> HttpResponse:
    query = parse.urlencode(params, doseq=True)
    target_url = url
    if query:
        separator = "&" if parse.urlparse(url).query else "?"
        target_url = f"{url}{separator}{query}"
    req = request.Request(target_url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - source URLs are fixed adapter endpoints.
            return HttpResponse(
                status=int(response.status),
                url=target_url,
                text=response.read().decode("utf-8"),
                headers={str(key): str(value) for key, value in response.headers.items()},
            )
    except Exception as error:  # pragma: no cover - wraps network errors.
        raise SourceAdapterError(f"HTTP request failed for {target_url}: {error}") from error


def urllib_get_bytes(url: str, params: dict[str, Any], headers: dict[str, str], timeout: int) -> HttpBinaryResponse:
    query = parse.urlencode(params, doseq=True)
    target_url = url
    if query:
        separator = "&" if parse.urlparse(url).query else "?"
        target_url = f"{url}{separator}{query}"
    req = request.Request(target_url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - source URLs are fixed adapter endpoints.
            return HttpBinaryResponse(
                status=int(response.status),
                url=str(response.geturl()),
                content=response.read(),
                headers={str(key): str(value) for key, value in response.headers.items()},
            )
    except Exception as error:  # pragma: no cover - wraps network errors.
        raise SourceAdapterError(f"HTTP request failed for {target_url}: {error}") from error


def urllib_post_json(url: str, params: dict[str, Any], headers: dict[str, str], timeout: int) -> HttpResponse:
    body = parse.urlencode(params, doseq=True).encode("utf-8")
    request_headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", **headers}
    req = request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - source URLs are fixed adapter endpoints.
            return HttpResponse(
                status=int(response.status),
                url=str(response.geturl()),
                text=response.read().decode("utf-8"),
                headers={str(key): str(value) for key, value in response.headers.items()},
            )
    except Exception as error:  # pragma: no cover - wraps network errors.
        raise SourceAdapterError(f"HTTP POST request failed for {url}: {error}") from error


def decode_response_text(content: bytes, headers: dict[str, str]) -> str:
    content_type = headers.get("Content-Type", "") or headers.get("content-type", "")
    declared = ""
    for piece in content_type.split(";"):
        piece = piece.strip()
        if piece.lower().startswith("charset="):
            declared = piece.split("=", 1)[1].strip()
            break
    encodings = [declared, "utf-8", "gb18030"]
    tried: set[str] = set()
    for encoding in encodings:
        if not encoding or encoding.lower() in tried:
            continue
        tried.add(encoding.lower())
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")
