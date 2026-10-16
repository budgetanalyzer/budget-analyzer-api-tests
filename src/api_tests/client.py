from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self
from urllib.parse import SplitResult, urlsplit

import httpx

from api_tests.config import EnvironmentConfig
from api_tests.logging import request_response_hooks
from api_tests.session import Session

LOGIN_INITIATION_REQUESTS_PER_SECOND: Final = 0.8
LOGIN_COMPLETION_REQUESTS_PER_SECOND: Final = 1.6
SESSION_OPERATIONS_REQUESTS_PER_SECOND: Final = 4.0
API_REQUESTS_PER_SECOND: Final = 8.0


class PublicRatePolicyName(StrEnum):
    LOGIN_INITIATION = "login-initiation"
    LOGIN_COMPLETION = "login-completion"
    SESSION_OPERATIONS = "session-operations"
    API_REQUESTS = "api-requests"


@dataclass(frozen=True)
class PublicRatePolicy:
    """One public Istio per-client path class and its conservative harness pace."""

    name: PublicRatePolicyName
    path_families: tuple[str, ...]
    requests_per_second: float

    def matches(self, request_target: str) -> bool:
        path = urlsplit(request_target).path
        return any(path == family or path.startswith(f"{family}/") for family in self.path_families)


LOGIN_INITIATION_RATE_POLICY: Final = PublicRatePolicy(
    name=PublicRatePolicyName.LOGIN_INITIATION,
    path_families=("/oauth2",),
    requests_per_second=LOGIN_INITIATION_REQUESTS_PER_SECOND,
)
LOGIN_COMPLETION_RATE_POLICY: Final = PublicRatePolicy(
    name=PublicRatePolicyName.LOGIN_COMPLETION,
    path_families=("/login/oauth2",),
    requests_per_second=LOGIN_COMPLETION_REQUESTS_PER_SECOND,
)
SESSION_OPERATIONS_RATE_POLICY: Final = PublicRatePolicy(
    name=PublicRatePolicyName.SESSION_OPERATIONS,
    path_families=("/auth", "/logout"),
    requests_per_second=SESSION_OPERATIONS_REQUESTS_PER_SECOND,
)
API_RATE_POLICY: Final = PublicRatePolicy(
    name=PublicRatePolicyName.API_REQUESTS,
    path_families=("/api",),
    requests_per_second=API_REQUESTS_PER_SECOND,
)
PUBLIC_RATE_POLICIES: Final = (
    LOGIN_INITIATION_RATE_POLICY,
    LOGIN_COMPLETION_RATE_POLICY,
    SESSION_OPERATIONS_RATE_POLICY,
    API_RATE_POLICY,
)


def classify_public_rate_policy(request_target: str) -> PublicRatePolicy | None:
    """Return the single public ingress policy applicable to a request target."""
    return next((policy for policy in PUBLIC_RATE_POLICIES if policy.matches(request_target)), None)


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


class GatewayRequestPacing:
    """Share four independent public-ingress request paces process-wide."""

    def __init__(
        self,
        *,
        login_initiation_pacer: RequestPacer,
        login_completion_pacer: RequestPacer,
        session_operations_pacer: RequestPacer,
        api_requests_pacer: RequestPacer,
    ) -> None:
        self._pacers = {
            PublicRatePolicyName.LOGIN_INITIATION: login_initiation_pacer,
            PublicRatePolicyName.LOGIN_COMPLETION: login_completion_pacer,
            PublicRatePolicyName.SESSION_OPERATIONS: session_operations_pacer,
            PublicRatePolicyName.API_REQUESTS: api_requests_pacer,
        }

    def wait(self, request_target: str) -> None:
        policy = classify_public_rate_policy(request_target)
        if policy is not None:
            self._pacers[policy.name].wait()


_SHARED_GATEWAY_REQUEST_PACING = GatewayRequestPacing(
    login_initiation_pacer=RequestPacer(
        requests_per_second=LOGIN_INITIATION_RATE_POLICY.requests_per_second
    ),
    login_completion_pacer=RequestPacer(
        requests_per_second=LOGIN_COMPLETION_RATE_POLICY.requests_per_second
    ),
    session_operations_pacer=RequestPacer(
        requests_per_second=SESSION_OPERATIONS_RATE_POLICY.requests_per_second
    ),
    api_requests_pacer=RequestPacer(requests_per_second=API_RATE_POLICY.requests_per_second),
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
