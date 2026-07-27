from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx

from api_tests.config import EnvironmentConfig
from api_tests.logging import request_response_hooks
from api_tests.session import SessionBundle, SessionContext


class GatewayClient:
    def __init__(
        self,
        config: EnvironmentConfig,
        *,
        artifact_path: Path | None = None,
        session_context: SessionContext | None = None,
        session_bundle: SessionBundle | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if session_context is not None and session_bundle is not None:
            raise ValueError("pass either session_context or session_bundle, not both")

        bundle = session_context.bundle if session_context is not None else session_bundle
        cookies = None
        if bundle is not None:
            session_cookie = bundle.primary.session_cookie
            cookies = {session_cookie.name: session_cookie.value}

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
