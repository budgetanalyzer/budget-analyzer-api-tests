from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self

import httpx

from api_tests.config import EnvironmentConfig
from api_tests.logging import request_response_hooks
from api_tests.session import Session

MAX_GATEWAY_REQUESTS_PER_SECOND: Final = 8.0


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
            self._next_request_at = request_at + self._minimum_interval
            delay = request_at - now
            if delay > 0:
                self._sleep(delay)


_SHARED_GATEWAY_REQUEST_PACER = RequestPacer(requests_per_second=MAX_GATEWAY_REQUESTS_PER_SECOND)


class GatewayClient:
    def __init__(
        self,
        config: EnvironmentConfig,
        *,
        artifact_path: Path | None = None,
        session: Session | None = None,
        transport: httpx.BaseTransport | None = None,
        request_pacer: RequestPacer | None = None,
    ) -> None:
        cookies = {session.cookie_name: session.cookie_value} if session is not None else None

        self.config = config
        self._request_pacer = request_pacer or _SHARED_GATEWAY_REQUEST_PACER
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
    def pace_shared_request() -> None:
        """Reserve a slot for a public-gateway request made outside httpx."""
        _SHARED_GATEWAY_REQUEST_PACER.wait()

    def api_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self.raw_request(method, _join_paths(self.config.api_base_path, path), **kwargs)

    def raw_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self._request_pacer.wait()
        return self._client.request(method, _absolute_path(path), **kwargs)


def _join_paths(prefix: str, path: str) -> str:
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}"


def _absolute_path(path: str) -> str:
    if not path.startswith("/"):
        return f"/{path}"
    return path
