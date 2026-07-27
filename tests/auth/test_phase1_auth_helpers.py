from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast

import httpx
import pytest
from playwright.sync_api import BrowserContext

from api_tests.auth import AuthConfigurationError, load_session_context
from api_tests.browser_login import BrowserLoginCredentials, read_session_cookie
from api_tests.client import GatewayClient
from api_tests.config import load_environment, normalized_non_secret_settings
from api_tests.identities import (
    PreprovisionedSessionError,
    acquire_browser_preprovisioned_session_bundle,
)
from api_tests.run_state import RUN_ID_PATTERN, RunState, generate_run_id, session_run_state
from api_tests.session import SessionBundle, SessionContext, SessionCookie, SessionIdentity


class FakeBrowserCookie(TypedDict, total=False):
    name: str
    value: str
    domain: str
    path: str
    httpOnly: bool


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


def test_read_session_cookie_extracts_http_only_cookie_metadata() -> None:
    class FakeContext:
        def cookies(self) -> list[FakeBrowserCookie]:
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


def test_browser_preprovisioned_adapter_logs_in_primary_and_secondary_sessions() -> None:
    config = load_environment("local")
    login_credentials: list[BrowserLoginCredentials] = []

    def fake_login(
        environment_config: object,
        credentials: BrowserLoginCredentials,
    ) -> SessionCookie:
        assert environment_config is config
        login_credentials.append(credentials)
        return SessionCookie(
            name="BA_SESSION",
            value=f"{credentials.username}-cookie",
            domain="app.budgetanalyzer.localhost",
            path="/",
        )

    bundle = acquire_browser_preprovisioned_session_bundle(
        config,
        RunState("ba-api-test-20260605T123456Z-1a2b3c4d"),
        environ={
            "BA_PRIMARY_TEST_USERNAME": "primary-user@example.invalid",
            "BA_PRIMARY_TEST_PASSWORD": "primary-password",
            "BA_SECONDARY_TEST_USERNAME": "secondary-user@example.invalid",
            "BA_SECONDARY_TEST_PASSWORD": "secondary-password",
        },
        login=fake_login,
    )

    assert [credentials.username for credentials in login_credentials] == [
        "primary-user@example.invalid",
        "secondary-user@example.invalid",
    ]
    assert bundle.primary == SessionIdentity(
        label="primary-user",
        session_cookie=SessionCookie(
            name="BA_SESSION",
            value="primary-user@example.invalid-cookie",
            domain="app.budgetanalyzer.localhost",
            path="/",
        ),
    )
    assert bundle.secondary == SessionIdentity(
        label="secondary-user",
        session_cookie=SessionCookie(
            name="BA_SESSION",
            value="secondary-user@example.invalid-cookie",
            domain="app.budgetanalyzer.localhost",
            path="/",
        ),
    )


def test_browser_preprovisioned_mode_fails_before_network_when_credentials_are_missing() -> None:
    config = load_environment("local")

    with pytest.raises(PreprovisionedSessionError, match="BA_PRIMARY_TEST_USERNAME"):
        acquire_browser_preprovisioned_session_bundle(
            config,
            RunState("ba-api-test-20260605T123456Z-1a2b3c4d"),
            environ={},
        )


def test_supplied_sessions_mode_reads_primary_and_secondary_cookies() -> None:
    config = load_environment("local")
    config = config.model_copy(
        update={"auth": config.auth.model_copy(update={"mode": "supplied_sessions"})}
    )

    auth_context = load_session_context(
        config,
        RunState("ba-api-test-20260605T123456Z-1a2b3c4d"),
        environ={
            "BA_PRIMARY_SESSION": "primary-cookie",
            "BA_SECONDARY_SESSION": "secondary-cookie",
        },
    )

    assert auth_context.mode == "supplied_sessions"
    assert auth_context.bundle == SessionBundle(
        primary=SessionIdentity(
            label="primary-session",
            session_cookie=SessionCookie(
                name="BA_SESSION",
                value="primary-cookie",
                domain="",
                path="/",
            ),
        ),
        secondary=SessionIdentity(
            label="secondary-session",
            session_cookie=SessionCookie(
                name="BA_SESSION",
                value="secondary-cookie",
                domain="",
                path="/",
            ),
        ),
    )


def test_supplied_sessions_mode_allows_missing_optional_secondary_cookie() -> None:
    config = load_environment("production")

    auth_context = load_session_context(
        config,
        RunState("ba-api-test-20260605T123456Z-1a2b3c4d"),
        environ={"BA_PRIMARY_SESSION": "production-smoke-cookie"},
    )

    assert auth_context.mode == "supplied_sessions"
    assert auth_context.bundle.secondary is None
    assert auth_context.bundle.primary.session_cookie == SessionCookie(
        name="BA_SESSION",
        value="production-smoke-cookie",
        domain="",
        path="/",
    )


def test_supplied_sessions_mode_requires_primary_cookie() -> None:
    config = load_environment("local")
    config = config.model_copy(
        update={"auth": config.auth.model_copy(update={"mode": "supplied_sessions"})}
    )

    with pytest.raises(AuthConfigurationError, match="BA_PRIMARY_SESSION"):
        load_session_context(
            config,
            RunState("ba-api-test-20260605T123456Z-1a2b3c4d"),
            environ={},
        )


def test_session_contract_reprs_do_not_expose_cookie_values_or_credentials() -> None:
    credentials = BrowserLoginCredentials(username="user@example.test", password="secret-password")
    cookie = SessionCookie(
        name="BA_SESSION",
        value="secret-cookie",
        domain="app.budgetanalyzer.localhost",
        path="/",
    )
    bundle = SessionBundle(
        primary=SessionIdentity(label="primary-session", session_cookie=cookie),
    )
    context = SessionContext(mode="supplied_sessions", bundle=bundle)

    rendered = "\n".join(
        [
            repr(credentials),
            repr(cookie),
            repr(bundle.primary),
            repr(bundle),
            repr(context),
        ]
    )

    assert "secret-password" not in rendered
    assert "secret-cookie" not in rendered
    assert "primary-session" in rendered


def test_normalized_environment_settings_do_not_include_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_environment("local")
    monkeypatch.setenv(config.auth.supplied_sessions.primary_session_env, "secret-cookie")
    assert config.auth.browser is not None
    monkeypatch.setenv(config.auth.browser.primary_password_env, "secret-browser-password")

    rendered = json.dumps(normalized_non_secret_settings(config), sort_keys=True)

    assert "secret-cookie" not in rendered
    assert "secret-browser-password" not in rendered
    assert config.auth.supplied_sessions.primary_session_env in rendered
    assert config.auth.browser.primary_password_env in rendered


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
        session_bundle=SessionBundle(
            primary=SessionIdentity(
                label="primary-session",
                session_cookie=SessionCookie(
                    name="BA_SESSION",
                    value="debug-cookie",
                    domain="",
                    path="/",
                ),
            )
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
