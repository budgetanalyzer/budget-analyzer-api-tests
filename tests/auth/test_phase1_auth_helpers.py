from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from playwright.sync_api import BrowserContext

from api_tests.auth import AuthConfigurationError, load_auth_context
from api_tests.auth0 import (
    Auth0CreatedUser,
    Auth0ManagementClient,
    Auth0ManagementSettings,
    Auth0ScopeError,
)
from api_tests.browser_login import BrowserLoginCredentials, SessionCookie, read_session_cookie
from api_tests.client import GatewayClient
from api_tests.config import load_environment
from api_tests.identities import create_run_identities
from api_tests.run_state import RUN_ID_PATTERN, RunState, generate_run_id, session_run_state


def test_generate_run_id_uses_required_format() -> None:
    run_id = generate_run_id(
        now=datetime(2026, 6, 5, 12, 34, 56, tzinfo=UTC),
        random_suffix="1a2b3c4d",
    )

    assert run_id == "ba-api-test-20260605T123456Z-1a2b3c4d"
    assert RUN_ID_PATTERN.fullmatch(run_id)


def test_session_run_state_is_stable_for_process_session() -> None:
    session_run_state.cache_clear()

    first = session_run_state()
    second = session_run_state()

    assert first is second
    assert RUN_ID_PATTERN.fullmatch(first.run_id)


def test_auth0_client_requests_management_token_and_creates_verified_user() -> None:
    settings = Auth0ManagementSettings(
        domain="tenant.example.auth0.com",
        client_id="client-id",
        client_secret="client-secret",
        connection="Username-Password-Authentication",
        test_email_domain="api-tests.example.invalid",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/oauth/token":
            assert request.url.scheme == "https"
            assert request.url.host == "tenant.example.auth0.com"
            return httpx.Response(200, json={"access_token": "management-token"})

        if request.url.path == "/api/v2/users":
            payload = json.loads(request.content.decode("utf-8"))
            assert request.headers["Authorization"] == "Bearer management-token"
            assert payload["connection"] == "Username-Password-Authentication"
            assert payload["email_verified"] is True
            assert payload["verify_email"] is False
            assert payload["email"].endswith("@api-tests.example.invalid")
            assert "password" in payload
            return httpx.Response(201, json={"user_id": "auth0|generated-user"})

        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = Auth0ManagementClient(settings, client=http_client)
        user = client.create_database_user(
            run_state=RunState("ba-api-test-20260605T123456Z-1a2b3c4d"),
            label="Primary User",
        )

    assert len(requests) == 2
    assert user.user_id == "auth0|generated-user"
    assert user.email.startswith("ba-api-test-20260605T123456Z-1a2b3c4d-primary-user-")
    assert user.password


def test_auth0_scope_errors_are_clear() -> None:
    settings = Auth0ManagementSettings(
        domain="https://tenant.example.auth0.com",
        client_id="client-id",
        client_secret="client-secret",
        connection="Username-Password-Authentication",
        test_email_domain="api-tests.example.invalid",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": "insufficient_scope",
                "error_description": "Insufficient scope, expected any of: create:users",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = Auth0ManagementClient(settings, client=http_client)
        with pytest.raises(Auth0ScopeError, match="create:users"):
            client.request_management_api_token()


def test_read_session_cookie_extracts_http_only_cookie_metadata() -> None:
    class FakeContext:
        def cookies(self) -> list[dict[str, object]]:
            return [
                {"name": "unrelated", "value": "x", "domain": "example.test", "path": "/"},
                {
                    "name": "BA_SESSION",
                    "value": "session-value",
                    "domain": "app.budgetanalyzer.localhost",
                    "path": "/",
                    "httpOnly": True,
                },
            ]

    cookie = read_session_cookie(cast(BrowserContext, FakeContext()), "BA_SESSION")

    assert cookie == SessionCookie(
        name="BA_SESSION",
        value="session-value",
        domain="app.budgetanalyzer.localhost",
        path="/",
    )


def test_create_run_identities_provisions_and_logs_in_primary_and_secondary_users() -> None:
    config = load_environment("local")
    created_labels: list[str] = []
    login_credentials: list[BrowserLoginCredentials] = []

    class FakeAuth0Client:
        def create_database_user(self, *, run_state: RunState, label: str) -> Auth0CreatedUser:
            created_labels.append(label)
            return Auth0CreatedUser(
                email=f"{label}@api-tests.example.invalid",
                password=f"{label}-password",
                user_id=f"auth0|{label}",
            )

    def fake_login(
        environment_config: object,
        credentials: BrowserLoginCredentials,
    ) -> SessionCookie:
        assert environment_config is config
        login_credentials.append(credentials)
        return SessionCookie(
            name="BA_SESSION",
            value=f"{credentials.email}-cookie",
            domain="app.budgetanalyzer.localhost",
            path="/",
        )

    identities = create_run_identities(
        config,
        RunState("ba-api-test-20260605T123456Z-1a2b3c4d"),
        auth0_client=cast(Auth0ManagementClient, FakeAuth0Client()),
        login=fake_login,
    )

    assert created_labels == ["primary-user", "secondary-user"]
    assert [credentials.email for credentials in login_credentials] == [
        "primary-user@api-tests.example.invalid",
        "secondary-user@api-tests.example.invalid",
    ]
    assert identities.primary_user.email == "primary-user@api-tests.example.invalid"
    assert identities.secondary_user.email == "secondary-user@api-tests.example.invalid"


def test_env_cookie_auth_mode_reads_local_debug_cookie() -> None:
    config = load_environment("local")
    config = config.model_copy(
        update={"auth": config.auth.model_copy(update={"mode": "env_cookie"})}
    )

    auth_context = load_auth_context(
        config,
        RunState("ba-api-test-20260605T123456Z-1a2b3c4d"),
        environ={"BA_SESSION": "debug-cookie"},
    )

    assert auth_context.mode == "env_cookie"
    assert auth_context.identities is None
    assert auth_context.session_cookie == SessionCookie(
        name="BA_SESSION",
        value="debug-cookie",
        domain="",
        path="/",
    )


def test_env_cookie_auth_mode_is_local_only() -> None:
    config = load_environment("staging")
    config = config.model_copy(
        update={"auth": config.auth.model_copy(update={"mode": "env_cookie"})}
    )

    with pytest.raises(AuthConfigurationError, match="only allowed for local"):
        load_auth_context(
            config,
            RunState("ba-api-test-20260605T123456Z-1a2b3c4d"),
            environ={"BA_SESSION": "debug-cookie"},
        )


def test_browser_auth0_mode_fails_before_network_when_management_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_environment("local")
    for env_var in (
        config.auth.auth0.management_domain_env,
        config.auth.auth0.management_client_id_env,
        config.auth.auth0.management_client_secret_env,
        config.auth.auth0.connection_env,
    ):
        monkeypatch.delenv(env_var, raising=False)

    with pytest.raises(RuntimeError, match="AUTH0_MGMT_DOMAIN"):
        load_auth_context(config, RunState("ba-api-test-20260605T123456Z-1a2b3c4d"))


def test_gateway_client_uses_origin_api_prefix_cookie_and_request_log(
    tmp_path: Path,
) -> None:
    config = load_environment("local")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "set-cookie": "BA_SESSION=secret",
            },
            json={
                "ok": True,
                "password": "secret-password",
                "access_token": "secret-token",
            },
        )

    artifact_path = tmp_path / "request-log.jsonl"
    with GatewayClient(
        config,
        artifact_path=artifact_path,
        session_cookie=SessionCookie(
            name="BA_SESSION",
            value="debug-cookie",
            domain="",
            path="/",
        ),
        transport=httpx.MockTransport(handler),
    ) as client:
        response = client.api_request("GET", "/transactions")

    assert response.status_code == 200
    assert str(requests[0].url) == "https://app.budgetanalyzer.localhost/api/transactions"
    assert requests[0].headers["cookie"] == "BA_SESSION=debug-cookie"

    log_entry = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert log_entry["method"] == "GET"
    assert log_entry["status"] == 200
    assert log_entry["response_headers"]["set-cookie"] == "[REDACTED]"
    assert "secret-password" not in log_entry["response_body_prefix"]
    assert "secret-token" not in log_entry["response_body_prefix"]
