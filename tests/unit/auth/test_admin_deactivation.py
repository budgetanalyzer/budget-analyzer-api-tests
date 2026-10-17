from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Self, cast

import httpx
import pytest

import api_tests.admin_deactivation as deactivation_module
from api_tests.admin_deactivation import (
    PartialDeactivationError,
    assert_sessions_revoked,
    deactivate_user_once,
)
from api_tests.client import GatewayClient, GatewayRequestPacing
from api_tests.config import EnvironmentConfig, load_environment, repository_root
from api_tests.openapi import OpenApiDocument, load_snapshot
from api_tests.session import Session


class _NoopPacing:
    def wait(self, request_target: str) -> None:
        del request_target


def test_partial_deactivation_reports_committed_state_without_retry(
    tmp_path: Path,
) -> None:
    config = load_environment("local")
    snapshot = load_snapshot(repository_root() / "schemas" / "openapi.json")
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(
                httpx.codes.SERVICE_UNAVAILABLE,
                json={"type": "SERVICE_UNAVAILABLE", "message": "Service Unavailable"},
            )
        return httpx.Response(
            httpx.codes.OK,
            json={
                "id": "usr-target",
                "idpSub": "idp|target",
                "email": "target@example.test",
                "displayName": "Target User",
                "status": "DEACTIVATED",
                "roleIds": [],
                "createdAt": "2026-10-17T12:00:00Z",
                "updatedAt": "2026-10-17T12:01:00Z",
                "deactivatedAt": "2026-10-17T12:01:00Z",
                "deactivatedBy": {"id": "usr-admin"},
            },
        )

    with (
        GatewayClient(
            config,
            artifact_path=tmp_path / "requests.jsonl",
            transport=httpx.MockTransport(handler),
            request_pacing=cast(GatewayRequestPacing, _NoopPacing()),
        ) as client,
        pytest.raises(PartialDeactivationError, match="no retry was sent") as raised,
    ):
        deactivate_user_once(client, "usr-target", snapshot)

    assert raised.value.observed_status == "DEACTIVATED"
    assert requests == [
        ("POST", "/api/v1/users/usr-target/deactivate"),
        ("GET", "/api/v1/users/usr-target"),
    ]


def test_stale_cookie_replay_uses_fresh_clients_and_restores_each_original_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_environment("local")
    unified_snapshot = load_snapshot(repository_root() / "schemas" / "openapi.json")
    session_snapshot = load_snapshot(repository_root() / "schemas" / "session-gateway-openapi.yaml")
    first = Session(config.session_cookie_name, "first-secret-cookie")
    second = Session(config.session_cookie_name, "second-secret-cookie")
    requests: list[tuple[str, str, str]] = []
    client_sessions: list[Session] = []

    class FakeGatewayClient:
        def __init__(
            self,
            environment_config: EnvironmentConfig,
            *,
            artifact_path: Path | None,
            session: Session,
        ) -> None:
            assert environment_config is config
            assert artifact_path is None
            self._session = session
            client_sessions.append(session)

        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            del exc_type, exc, traceback

        def raw_request(self, method: str, path: str) -> httpx.Response:
            return self._response("raw", method, path)

        def api_request(self, method: str, path: str) -> httpx.Response:
            return self._response("api", method, path)

        def _response(self, kind: str, method: str, path: str) -> httpx.Response:
            requests.append((self._session.cookie_value, kind, path))
            headers = {}
            if path == "/auth/v1/session":
                headers = {"set-cookie": f"{config.session_cookie_name}=; Max-Age=0; Path=/"}
            request = httpx.Request(method, f"{config.origin}{path}")
            return httpx.Response(
                httpx.codes.UNAUTHORIZED,
                headers=headers,
                json={"type": "UNAUTHORIZED", "message": "Authentication required"},
                request=request,
            )

    monkeypatch.setattr(deactivation_module, "GatewayClient", FakeGatewayClient)

    assert_sessions_revoked(
        config,
        (first, second),
        request_log_path=None,
        session_gateway_openapi_snapshot=session_snapshot,
        openapi_snapshot=unified_snapshot,
    )

    assert requests == [
        ("first-secret-cookie", "raw", "/auth/v1/user"),
        ("first-secret-cookie", "api", "/v1/currencies"),
        ("first-secret-cookie", "raw", "/auth/v1/session"),
        ("second-secret-cookie", "raw", "/auth/v1/user"),
        ("second-secret-cookie", "api", "/v1/currencies"),
        ("second-secret-cookie", "raw", "/auth/v1/session"),
    ]
    assert client_sessions == [first, first, first, second, second, second]
    assert "first-secret-cookie" not in repr(first)
    assert "second-secret-cookie" not in repr(second)


def test_successful_deactivation_result_is_schema_backed(tmp_path: Path) -> None:
    config = load_environment("local")
    snapshot: OpenApiDocument = load_snapshot(repository_root() / "schemas" / "openapi.json")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "POST"
        return httpx.Response(
            httpx.codes.OK,
            json={
                "userId": "usr-target",
                "status": "DEACTIVATED",
                "rolesRemoved": 1,
                "sessionsRevoked": True,
            },
        )

    with GatewayClient(
        config,
        artifact_path=tmp_path / "requests.jsonl",
        transport=httpx.MockTransport(handler),
        request_pacing=cast(GatewayRequestPacing, _NoopPacing()),
    ) as client:
        result = deactivate_user_once(client, "usr-target", snapshot)

    assert result.user_id == "usr-target"
    assert result.roles_removed == 1
    assert result.sessions_revoked is True
    assert calls == 1
