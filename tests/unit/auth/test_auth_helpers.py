from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self, TypedDict, cast

import httpx
import pytest
from playwright.sync_api import BrowserContext

import api_tests.browser_login as browser_login_module
from api_tests.assertions import assert_status
from api_tests.auth import AuthConfigurationError, acquire_browser_session, load_supplied_session
from api_tests.browser_login import BrowserLoginCredentials, read_session_cookie, verify_session
from api_tests.client import GatewayClient
from api_tests.config import load_environment
from api_tests.logging import MAX_BODY_BYTES
from api_tests.run_state import RUN_ID_PATTERN, generate_run_id
from api_tests.session import (
    IndistinctSessionsError,
    MissingSecondarySessionError,
    Session,
    ensure_distinct_sessions,
)


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


def test_read_session_cookie_extracts_session_value() -> None:
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

    session = read_session_cookie(cast(BrowserContext, FakeContext()), "BA_SESSION")

    assert session == Session(cookie_name="BA_SESSION", cookie_value="session-value")


def test_session_verification_uses_gateway_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_environment("local")
    session = Session(cookie_name="BA_SESSION", cookie_value="session-value")
    calls: list[tuple[object, Session, str, str]] = []

    class FakeGatewayClient:
        def __init__(self, environment_config: object, *, session: Session) -> None:
            self._config = environment_config
            self._session = session

        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        def raw_request(self, method: str, path: str) -> httpx.Response:
            calls.append((self._config, self._session, method, path))
            return httpx.Response(httpx.codes.OK)

    monkeypatch.setattr(browser_login_module, "GatewayClient", FakeGatewayClient)

    verify_session(config, session)

    assert calls == [(config, session, "GET", "/auth/v1/user")]


def test_browser_acquisition_logs_in_only_requested_user() -> None:
    config = load_environment("local")
    login_credentials: list[BrowserLoginCredentials] = []

    def fake_login(
        environment_config: object,
        credentials: BrowserLoginCredentials,
    ) -> Session:
        assert environment_config is config
        login_credentials.append(credentials)
        return Session(
            cookie_name="BA_SESSION",
            cookie_value=f"{credentials.username}-cookie",
        )

    session = acquire_browser_session(
        config,
        "primary",
        environ={
            "BA_PRIMARY_TEST_USERNAME": "primary-user@example.invalid",
            "BA_PRIMARY_TEST_PASSWORD": "primary-password",
        },
        login=fake_login,
    )

    assert [credentials.username for credentials in login_credentials] == [
        "primary-user@example.invalid"
    ]
    assert session == Session(
        cookie_name="BA_SESSION",
        cookie_value="primary-user@example.invalid-cookie",
    )


def test_browser_acquisition_fails_before_login_for_partial_environment_pair() -> None:
    config = load_environment("local")
    login_called = False

    def unexpected_login(
        environment_config: object,
        credentials: BrowserLoginCredentials,
    ) -> Session:
        del environment_config, credentials
        nonlocal login_called
        login_called = True
        raise AssertionError("login should not run")

    with pytest.raises(AuthConfigurationError, match="set both"):
        acquire_browser_session(
            config,
            "primary",
            environ={"BA_PRIMARY_TEST_USERNAME": "partial@test.com"},
            login=unexpected_login,
        )

    assert login_called is False


def test_supplied_sessions_load_primary_and_secondary_independently() -> None:
    config = load_environment("local")
    source = {
        "BA_PRIMARY_SESSION": "primary-cookie",
        "BA_SECONDARY_SESSION": "secondary-cookie",
    }

    primary = load_supplied_session(config, "primary", environ=source)
    secondary = load_supplied_session(config, "secondary", environ=source)

    assert primary == Session(cookie_name="BA_SESSION", cookie_value="primary-cookie")
    assert secondary == Session(cookie_name="BA_SESSION", cookie_value="secondary-cookie")


def test_authorization_sessions_must_be_distinct() -> None:
    session = Session(cookie_name="BA_SESSION", cookie_value="reused-cookie")

    with pytest.raises(IndistinctSessionsError, match="same authenticated session"):
        ensure_distinct_sessions(session, session)

    ensure_distinct_sessions(
        Session(cookie_name="BA_SESSION", cookie_value="primary-cookie"),
        Session(cookie_name="BA_SESSION", cookie_value="secondary-cookie"),
    )


def test_production_allows_primary_without_configuring_secondary() -> None:
    config = load_environment("production")

    primary = load_supplied_session(
        config,
        "primary",
        environ={"BA_PRIMARY_SESSION": "production-smoke-cookie"},
    )

    assert primary == Session(
        cookie_name="BA_SESSION",
        cookie_value="production-smoke-cookie",
    )
    with pytest.raises(MissingSecondarySessionError, match="does not configure"):
        load_supplied_session(config, "secondary", environ={})


def test_supplied_session_requires_requested_cookie() -> None:
    config = load_environment("local")

    with pytest.raises(AuthConfigurationError, match="BA_PRIMARY_SESSION"):
        load_supplied_session(config, "primary", environ={})


def test_secret_value_representations_are_redacted() -> None:
    credentials = BrowserLoginCredentials(username="user@example.test", password="secret-password")
    session = Session(cookie_name="BA_SESSION", cookie_value="secret-cookie")

    rendered = "\n".join((repr(credentials), repr(session)))

    assert "user@example.test" not in rendered
    assert "secret-password" not in rendered
    assert "secret-cookie" not in rendered
    assert "BA_SESSION" in rendered


def test_environment_serialization_contains_names_not_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_environment("local")
    monkeypatch.setenv(config.auth.supplied_sessions.primary_session_env, "secret-cookie")
    assert config.auth.browser is not None
    monkeypatch.setenv(config.auth.browser.primary_password_env, "secret-browser-password")

    rendered = json.dumps(config.model_dump(mode="json"), sort_keys=True)

    assert "secret-cookie" not in rendered
    assert "secret-browser-password" not in rendered
    assert config.auth.supplied_sessions.primary_session_env in rendered
    assert config.auth.browser.primary_password_env in rendered


def test_gateway_client_uses_origin_api_prefix_session_and_request_log(
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
                "email": "ba-api-test-sensitive@test.com",
                "name": "ba-api-test-sensitive@test.com",
                "username": "sensitive-username",
                "password": "secret-password",
                "access_token": "secret-token",
            },
        )

    artifact_path = tmp_path / "request-log.jsonl"
    with GatewayClient(
        config,
        artifact_path=artifact_path,
        session=Session(cookie_name="BA_SESSION", cookie_value="debug-cookie"),
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
    assert "ba-api-test-sensitive@test.com" not in log_entry["response_body_prefix"]
    assert "sensitive-username" not in log_entry["response_body_prefix"]
    assert "secret-password" not in log_entry["response_body_prefix"]
    assert "secret-token" not in log_entry["response_body_prefix"]


def test_status_failure_context_redacts_sensitive_response_values() -> None:
    response = httpx.Response(
        400,
        headers={"content-type": "application/json"},
        json={
            "password": "secret-password",
            "previewImportToken": "v2.secret-payload.secret-signature",
            "email": "ba-api-test-sensitive@test.com",
        },
    )

    with pytest.raises(AssertionError) as exc_info:
        assert_status(response, httpx.codes.OK)

    message = str(exc_info.value)
    assert "secret-password" not in message
    assert "secret-payload" not in message
    assert "ba-api-test-sensitive@test.com" not in message
    assert "[REDACTED]" in message


def test_oversized_json_is_redacted_before_artifact_and_failure_context_truncation(
    tmp_path: Path,
) -> None:
    cookie_secret = "oversized-cookie-secret"
    preview_token = "v2.preview-payload.preview-signature"
    leaked_token_prefix = "v2.previ"
    body_before_filler = f'{{"cookie":{json.dumps(cookie_secret)},"zz_filler":"'
    body_before_token = '","preview":"'
    token_offset = MAX_BODY_BYTES - len(leaked_token_prefix)
    filler_length = token_offset - len((body_before_filler + body_before_token).encode("utf-8"))
    body = body_before_filler + ("x" * filler_length) + body_before_token + preview_token + '"}'

    encoded_body = body.encode("utf-8")
    assert encoded_body[token_offset : token_offset + len(preview_token)] == preview_token.encode()
    assert token_offset < MAX_BODY_BYTES < token_offset + len(preview_token)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            headers={"content-type": "application/json"},
            content=encoded_body,
        )

    artifact_path = tmp_path / "request-log.jsonl"
    with GatewayClient(
        load_environment("local"),
        artifact_path=artifact_path,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = client.api_request("GET", "/oversized-response")

    log_entry = json.loads(artifact_path.read_text(encoding="utf-8"))
    logged_body = log_entry["response_body_prefix"]
    assert len(logged_body.encode("utf-8")) <= MAX_BODY_BYTES
    assert cookie_secret not in logged_body
    assert leaked_token_prefix not in logged_body
    assert logged_body.count("[REDACTED]") == 2

    with pytest.raises(AssertionError) as exc_info:
        assert_status(response, httpx.codes.OK)

    failure_context = str(exc_info.value)
    assert cookie_secret not in failure_context
    assert leaked_token_prefix not in failure_context
    assert "[REDACTED]" in failure_context
