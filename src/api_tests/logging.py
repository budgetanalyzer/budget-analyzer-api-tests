from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx

from api_tests.config import repository_root

MAX_BODY_BYTES = 4096
REQUEST_STARTED_AT = "budget_analyzer_request_started_at"
SENSITIVE_HEADER_NAMES = {"authorization", "cookie", "location", "set-cookie"}
SENSITIVE_OAUTH_QUERY_NAMES = {
    "client_id",
    "code",
    "code_challenge",
    "id_token_hint",
    "nonce",
    "state",
}
SENSITIVE_PROFILE_QUERY_NAMES = {"displayname", "email", "id", "idpsub"}
SENSITIVE_KEY_RE = re.compile(
    r"(authorization|cookie|email|username|password|client_secret|access_token|refresh_token|"
    r"id_token|token)",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(
    r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9.-])",
    re.IGNORECASE,
)
PREVIEW_TOKEN_RE = re.compile(r"\bv2\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
USER_PROFILE_KEYS = frozenset({"displayname", "email", "id", "idpsub", "name", "sub"})
IDENTITY_REFERENCE_KEYS = frozenset(
    {"createdby", "deactivatedby", "deletedby", "ownerid", "updatedby", "userid"}
)
HttpxEventHook = Callable[..., object]
DEFAULT_REQUEST_LOG_PATH = (
    repository_root()
    / "artifacts"
    / "runs"
    / f"direct-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    / "request-log.jsonl"
)


def redacted_response_context(response: httpx.Response, *, limit: int = 500) -> str:
    """Return concise response diagnostics that are safe to include in failures."""
    body = _redact_body_prefix(response)
    if len(body) > limit:
        body = f"{body[:limit]}..."
    return (
        f"HTTP {response.status_code}; content-type="
        f"{response.headers.get('content-type', '<missing>')!r}; body={body!r}"
    )


def redact_sensitive_text(value: str) -> str:
    """Redact credential-like values from diagnostics outside artifact logging."""
    redacted = _redact_sensitive_text(value)
    return re.sub(
        r"(?i)([?&](?:client_id|code|code_challenge|id_token_hint|nonce|state)=)[^&#\s]+",
        r"\1[REDACTED]",
        redacted,
    )


def request_response_hooks(
    *,
    artifact_path: Path | None = None,
) -> dict[str, list[HttpxEventHook]]:
    path = artifact_path or DEFAULT_REQUEST_LOG_PATH
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
    text = response.content.decode(response.encoding or "utf-8", errors="replace")

    content_type = response.headers.get("content-type", "")
    if "json" not in content_type.lower():
        return _utf8_prefix(_redact_sensitive_text(text), MAX_BODY_BYTES)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        sanitized = _redact_sensitive_text(text)
    else:
        sanitized = json.dumps(
            _redact_json(payload, redact_user_profile=_is_user_profile_response(response)),
            sort_keys=True,
        )

    return _utf8_prefix(sanitized, MAX_BODY_BYTES)


def _utf8_prefix(value: str, byte_limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= byte_limit:
        return value
    return encoded[:byte_limit].decode("utf-8", errors="ignore")


def _redact_json(value: object, *, redact_user_profile: bool = False) -> object:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if SENSITIVE_KEY_RE.search(str(key))
                or str(key).casefold() in IDENTITY_REFERENCE_KEYS
                or (redact_user_profile and str(key).casefold() in USER_PROFILE_KEYS)
                else _redact_json(item, redact_user_profile=redact_user_profile)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item, redact_user_profile=redact_user_profile) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


def _redact_sensitive_text(value: str) -> str:
    redacted = EMAIL_RE.sub("[REDACTED]", value)
    redacted = PREVIEW_TOKEN_RE.sub("[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)(authorization:\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)((?:access_token|refresh_token|id_token|password|client_secret|email|username)"
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
    parsed = urlsplit(url)
    path = re.sub(
        r"(?i)(/v1/users/)[^/?#]+",
        r"\1[REDACTED]",
        parsed.path,
    )
    redacted_query = urlencode(
        [
            (
                name,
                "[REDACTED]"
                if name.casefold() in SENSITIVE_OAUTH_QUERY_NAMES | SENSITIVE_PROFILE_QUERY_NAMES
                else value,
            )
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    return _redact_sensitive_text(
        urlunsplit((parsed.scheme, parsed.netloc, path, redacted_query, parsed.fragment))
    )


def _is_user_profile_response(response: httpx.Response) -> bool:
    try:
        path = response.request.url.path
    except RuntimeError:
        return False
    return bool(re.search(r"(?i)(?:/v1/users(?:/|$)|/auth/v1/user$)", path))
