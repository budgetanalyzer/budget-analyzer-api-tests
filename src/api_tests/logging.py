from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from api_tests.config import repository_root

MAX_BODY_BYTES = 4096
REQUEST_STARTED_AT = "budget_analyzer_request_started_at"
SENSITIVE_HEADER_NAMES = {"authorization", "cookie", "set-cookie"}
SENSITIVE_KEY_RE = re.compile(
    r"(authorization|cookie|password|client_secret|access_token|refresh_token|id_token|token)",
    re.IGNORECASE,
)


def request_response_hooks(
    *,
    artifact_path: Path | None = None,
) -> dict[str, list[Callable[[Any], None]]]:
    path = artifact_path or repository_root() / "artifacts" / "request-log.jsonl"
    return {
        "request": [_record_request_start],
        "response": [_make_response_logger(path)],
    }


def _record_request_start(request: httpx.Request) -> None:
    request.extensions[REQUEST_STARTED_AT] = time.perf_counter()


def _make_response_logger(artifact_path: Path) -> Callable[[httpx.Response], None]:
    def log_response(response: httpx.Response) -> None:
        response.read()
        started_at = response.request.extensions.get(REQUEST_STARTED_AT)
        elapsed_seconds = None
        if isinstance(started_at, float):
            elapsed_seconds = round(time.perf_counter() - started_at, 6)

        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        with artifact_path.open("a", encoding="utf-8") as log_file:
            log_file.write(
                json.dumps(
                    {
                        "method": response.request.method,
                        "url": _redact_url(str(response.request.url)),
                        "status": response.status_code,
                        "elapsed_seconds": elapsed_seconds,
                        "response_headers": _redact_headers(response.headers),
                        "response_body_prefix": _redact_body_prefix(response),
                    },
                    sort_keys=True,
                )
            )
            log_file.write("\n")

    return log_response


def _redact_headers(headers: httpx.Headers) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        redacted[name] = "[REDACTED]" if name.lower() in SENSITIVE_HEADER_NAMES else value
    return redacted


def _redact_body_prefix(response: httpx.Response) -> str:
    body_prefix = response.content[:MAX_BODY_BYTES]
    text = body_prefix.decode(response.encoding or "utf-8", errors="replace")

    content_type = response.headers.get("content-type", "")
    if "json" not in content_type.lower():
        return _redact_sensitive_text(text)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _redact_sensitive_text(text)

    return json.dumps(_redact_json(payload), sort_keys=True)


def _redact_json(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SENSITIVE_KEY_RE.search(str(key)) else _redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


def _redact_sensitive_text(value: str) -> str:
    redacted = re.sub(
        r"(?i)(authorization:\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        value,
    )
    redacted = re.sub(
        r"(?i)((?:access_token|refresh_token|id_token|password|client_secret)"
        r'["\']?\s*[:=]\s*["\']?)[^"\'\s,;}]+',
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)((?:cookie|set-cookie)\s*:\s*)[^\n\r]+",
        r"\1[REDACTED]",
        redacted,
    )
    return redacted


def _redact_url(url: str) -> str:
    return _redact_sensitive_text(url)
