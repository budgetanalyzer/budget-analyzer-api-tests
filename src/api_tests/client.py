from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self
from urllib.parse import SplitResult, urlsplit

import httpx

from api_tests.config import EnvironmentConfig
from api_tests.logging import request_response_hooks
from api_tests.session import Session

MAX_GATEWAY_REQUESTS_PER_SECOND: Final = 8.0
AUTH_SENSITIVE_MAX_TOKENS: Final = 10
AUTH_SENSITIVE_TOKENS_PER_FILL: Final = 10
AUTH_SENSITIVE_FILL_INTERVAL_SECONDS: Final = 60.0


@dataclass(frozen=True)
class AuthSensitiveRatePolicy:
    """Public Istio descriptor matched before Session Gateway routing."""

    exact_paths: tuple[str, ...]
    path_families: tuple[str, ...]
    max_tokens: int
    tokens_per_fill: int
    fill_interval_seconds: float

    def matches(self, request_target: str) -> bool:
        path = urlsplit(request_target).path
        if path in self.exact_paths:
            return True
        return any(path == family or path.startswith(f"{family}/") for family in self.path_families)


AUTH_SENSITIVE_RATE_POLICY: Final = AuthSensitiveRatePolicy(
    exact_paths=("/login",),
    path_families=("/logout", "/auth", "/oauth2", "/login/oauth2"),
    max_tokens=AUTH_SENSITIVE_MAX_TOKENS,
    tokens_per_fill=AUTH_SENSITIVE_TOKENS_PER_FILL,
    fill_interval_seconds=AUTH_SENSITIVE_FILL_INTERVAL_SECONDS,
)


class RequestPacer:
    """Serialize request starts at a fixed maximum rate."""

    def __init__(
        self,
        *,
        requests_per_second: float,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be greater than zero")

        self._minimum_interval = 1.0 / requests_per_second
        self._monotonic = monotonic
        self._sleep = sleep
        self._next_request_at: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._monotonic()
            request_at = now if self._next_request_at is None else max(now, self._next_request_at)
            delay = request_at - now
            if delay > 0:
                self._sleep(delay)
            self._next_request_at = self._monotonic() + self._minimum_interval


class DiscreteTokenBucketPacer:
    """Reserve from a fixed-capacity bucket with non-continuous refills."""

    def __init__(
        self,
        *,
        max_tokens: int,
        tokens_per_fill: int,
        fill_interval_seconds: float,
        conservative_initial_wait: bool,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be greater than zero")
        if tokens_per_fill <= 0:
            raise ValueError("tokens_per_fill must be greater than zero")
        if fill_interval_seconds <= 0:
            raise ValueError("fill_interval_seconds must be greater than zero")

        self._max_tokens = max_tokens
        self._tokens_per_fill = tokens_per_fill
        self._fill_interval_seconds = fill_interval_seconds
        self._tokens = 0 if conservative_initial_wait else max_tokens
        self._monotonic = monotonic
        self._sleep = sleep
        self._next_fill_at: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._monotonic()
            if self._next_fill_at is None:
                self._next_fill_at = now + self._fill_interval_seconds

            self._refill(now)
            while self._tokens == 0:
                delay = self._next_fill_at - now
                if delay > 0:
                    self._sleep(delay)
                now = self._monotonic()
                self._refill(now)

            self._tokens -= 1

    def _refill(self, now: float) -> None:
        if self._next_fill_at is None or now < self._next_fill_at:
            return

        elapsed_fill_intervals = int((now - self._next_fill_at) // self._fill_interval_seconds) + 1
        self._tokens = min(
            self._max_tokens,
            self._tokens + (elapsed_fill_intervals * self._tokens_per_fill),
        )
        self._next_fill_at += elapsed_fill_intervals * self._fill_interval_seconds


class GatewayRequestPacing:
    """Apply all public-ingress reservations to one request start."""

    def __init__(
        self,
        *,
        gateway_pacer: RequestPacer,
        auth_sensitive_pacer: DiscreteTokenBucketPacer,
        auth_sensitive_policy: AuthSensitiveRatePolicy = AUTH_SENSITIVE_RATE_POLICY,
    ) -> None:
        self._gateway_pacer = gateway_pacer
        self._auth_sensitive_pacer = auth_sensitive_pacer
        self._auth_sensitive_policy = auth_sensitive_policy

    def wait(self, request_target: str) -> None:
        if self._auth_sensitive_policy.matches(request_target):
            self._auth_sensitive_pacer.wait()
        self._gateway_pacer.wait()


_SHARED_GATEWAY_REQUEST_PACING = GatewayRequestPacing(
    gateway_pacer=RequestPacer(requests_per_second=MAX_GATEWAY_REQUESTS_PER_SECOND),
    auth_sensitive_pacer=DiscreteTokenBucketPacer(
        max_tokens=AUTH_SENSITIVE_RATE_POLICY.max_tokens,
        tokens_per_fill=AUTH_SENSITIVE_RATE_POLICY.tokens_per_fill,
        fill_interval_seconds=AUTH_SENSITIVE_RATE_POLICY.fill_interval_seconds,
        conservative_initial_wait=True,
    ),
)


class GatewayClient:
    def __init__(
        self,
        config: EnvironmentConfig,
        *,
        artifact_path: Path | None = None,
        session: Session | None = None,
        transport: httpx.BaseTransport | None = None,
        request_pacing: GatewayRequestPacing | None = None,
    ) -> None:
        cookies = {session.cookie_name: session.cookie_value} if session is not None else None

        self.config = config
        self._request_pacing = request_pacing or _SHARED_GATEWAY_REQUEST_PACING
        self._client = httpx.Client(
            base_url=config.origin,
            cookies=cookies,
            event_hooks=request_response_hooks(artifact_path=artifact_path),
            timeout=config.timeouts.request_seconds,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def pace_shared_request(request_target: str) -> None:
        """Reserve a slot for a public-gateway request made outside httpx."""
        _SHARED_GATEWAY_REQUEST_PACING.wait(request_target)

    def api_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self.raw_request(method, _join_paths(self.config.api_base_path, path), **kwargs)

    def raw_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        request_target = _absolute_path(path)
        self._request_pacing.wait(request_target)
        return self._client.request(method, request_target, **kwargs)


def is_same_origin(request_url: str, origin: str) -> bool:
    """Return whether an absolute browser URL uses the selected gateway origin."""
    request_origin = _normalized_origin(urlsplit(request_url))
    selected_origin = _normalized_origin(urlsplit(origin))
    return request_origin is not None and request_origin == selected_origin


def request_path_from_url(request_url: str) -> str:
    """Return only the non-secret path needed for ingress classification."""
    return urlsplit(request_url).path


def _join_paths(prefix: str, path: str) -> str:
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}"


def _absolute_path(path: str) -> str:
    if not path.startswith("/"):
        return f"/{path}"
    return path


def _normalized_origin(parsed: SplitResult) -> tuple[str, str, int] | None:
    scheme = parsed.scheme.casefold()
    host = parsed.hostname
    if scheme not in {"http", "https"} or host is None:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host.casefold(), port
