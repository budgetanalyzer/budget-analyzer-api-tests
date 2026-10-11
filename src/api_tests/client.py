from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx

from api_tests.config import EnvironmentConfig
from api_tests.logging import request_response_hooks
from api_tests.session import Session


class GatewayClient:
    def __init__(
        self,
        config: EnvironmentConfig,
        *,
        artifact_path: Path | None = None,
        session: Session | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        cookies = {session.cookie_name: session.cookie_value} if session is not None else None

        self.config = config
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

    def api_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self.raw_request(method, _join_paths(self.config.api_base_path, path), **kwargs)

    def raw_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self._client.request(method, _absolute_path(path), **kwargs)


def _join_paths(prefix: str, path: str) -> str:
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}"


def _absolute_path(path: str) -> str:
    if not path.startswith("/"):
        return f"/{path}"
    return path
