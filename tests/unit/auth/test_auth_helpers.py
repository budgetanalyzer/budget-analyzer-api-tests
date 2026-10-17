from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self, TypedDict, cast

import httpx
import pytest
from playwright.sync_api import BrowserContext, CDPSession, Page, Response
from playwright.sync_api import Error as PlaywrightError

import api_tests.auth as auth_module
import api_tests.browser_login as browser_login_module
from api_tests.assertions import assert_status
from api_tests.auth import (
    AuthConfigurationError,
    acquire_browser_session,
    admin_promotion_message,
    load_existing_browser_credentials,
    load_supplied_session,
)
from api_tests.browser_login import (
    BrowserFlowObserver,
    BrowserLoginCredentials,
    BrowserLoginError,
    read_session_cookie,
    verify_session,
)
from api_tests.client import GatewayClient
from api_tests.config import load_environment, with_session_mode
from api_tests.logging import (
    MAX_BODY_BYTES,
    _redact_headers,
    _redact_url,
    redacted_response_context,
)
from api_tests.run_state import RUN_ID_PATTERN, generate_run_id
from api_tests.session import (
    IndistinctSessionsError,
    MissingAdminSessionError,
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


class FakeBrowserRequest:
    def __init__(self, url: str, method: str = "GET") -> None:
        self.url = url
        self.method = method


class FakeCdpSession:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.commands: list[tuple[str, object | None]] = []

    def on(self, event: str, handler: object) -> None:
        self.handlers[event] = handler

    def send(self, method: str, params: object | None = None) -> dict[str, object]:
        self.commands.append((method, params))
        return {}


class FakeBrowserResponse:
    def __init__(self, url: str, *, method: str = "GET", status: int = 302) -> None:
        self.url = url
        self.request = FakeBrowserRequest(url, method)
        self.status = status


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


def test_browser_request_interception_paces_only_same_origin_and_strips_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway_origin = "https://app.budgetanalyzer.localhost"
    paced_paths: list[str] = []
    request_urls = [
        f"{gateway_origin}/assets/app.js?cache=public",
        f"{gateway_origin}/login/oauth2/code/idp?probe=callback",
        "https://identity.example.test/auth/v1/user?probe=provider",
    ]
    session = FakeCdpSession()
    monkeypatch.setattr(
        GatewayClient,
        "pace_shared_request",
        staticmethod(paced_paths.append),
    )

    for index, request_url in enumerate(request_urls):
        browser_login_module._continue_paced_gateway_request(
            cast(CDPSession, session),
            {"requestId": f"request-{index}", "request": {"url": request_url}},
            gateway_origin=gateway_origin,
        )

    assert paced_paths == ["/assets/app.js", "/login/oauth2/code/idp"]
    assert session.commands == [
        ("Fetch.continueRequest", {"requestId": "request-0"}),
        ("Fetch.continueRequest", {"requestId": "request-1"}),
        ("Fetch.continueRequest", {"requestId": "request-2"}),
    ]


def test_browser_registers_request_stage_same_origin_interceptor() -> None:
    session = FakeCdpSession()

    class FakeContext:
        def new_cdp_session(self, page: Page) -> CDPSession:
            del page
            return cast(CDPSession, session)

    browser_login_module._pace_public_gateway_requests(
        cast(BrowserContext, FakeContext()),
        cast(Page, object()),
        load_environment("local"),
    )

    assert callable(session.handlers["Fetch.requestPaused"])
    assert session.commands == [
        (
            "Fetch.enable",
            {
                "patterns": [
                    {
                        "urlPattern": "https://app.budgetanalyzer.localhost/*",
                        "requestStage": "Request",
                    }
                ]
            },
        )
    ]


def test_browser_flow_observations_drop_queries_and_classify_external_hosts() -> None:
    config = load_environment("local")
    observer = BrowserFlowObserver(config)

    observer.record_response(
        cast(
            Response,
            FakeBrowserResponse(
                f"{config.origin}/login/oauth2/code/idp?code=sensitive&state=sensitive"
            ),
        )
    )
    observer.record_response(
        cast(
            Response,
            FakeBrowserResponse(
                "https://tenant.us.auth0.com/authorize?state=sensitive",
            ),
        )
    )

    observations = observer.result()
    assert observations[0].same_origin_path == "/login/oauth2/code/idp"
    assert observations[0].external_host_classification is None
    assert observations[1].same_origin_path is None
    assert observations[1].external_host_classification == "identity-provider"
    rendered = repr(observations)
    assert "sensitive" not in rendered
    assert "tenant.us.auth0.com" not in rendered


def test_browser_flow_observer_rejects_an_unpaced_rate_limited_response() -> None:
    config = load_environment("local")
    observer = BrowserFlowObserver(config)
    observer.record_response(
        cast(
            Response,
            FakeBrowserResponse(f"{config.origin}/login/oauth2/code/idp?code=sensitive"),
        )
    )

    with pytest.raises(BrowserLoginError, match="bypassed shared pacing"):
        observer.assert_rate_limited_responses_were_paced()


@pytest.mark.parametrize(
    "unsafe_path",
    ["https://attacker.example/path", "//attacker.example/path", "/safe#fragment"],
)
def test_expected_browser_return_path_rejects_unsafe_values(unsafe_path: str) -> None:
    with pytest.raises(BrowserLoginError, match="safe same-origin path"):
        browser_login_module._validate_expected_return_path(unsafe_path)


def test_admin_browser_acquisition_accepts_pre_and_post_promotion_destinations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_environment("staging")
    observed_return_paths: list[tuple[str, ...] | None] = []

    def fake_browser_login(
        environment_config: object,
        credentials: BrowserLoginCredentials,
        *,
        expected_return_paths: tuple[str, ...] | None = None,
    ) -> Session:
        assert environment_config is config
        assert credentials.username == "staging-admin@example.invalid"
        observed_return_paths.append(expected_return_paths)
        return Session(cookie_name="BA_SESSION", cookie_value="admin-cookie")

    monkeypatch.setattr(auth_module, "login_with_browser", fake_browser_login)

    session = acquire_browser_session(
        config,
        "admin",
        environ={
            "BA_ADMIN_TEST_USERNAME": "staging-admin@example.invalid",
            "BA_ADMIN_TEST_PASSWORD": "admin-password",
        },
    )

    assert session == Session(cookie_name="BA_SESSION", cookie_value="admin-cookie")
    assert observed_return_paths == [("/", "/admin")]


def test_primary_browser_acquisition_accepts_only_normal_default_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_environment("staging")
    observed_return_paths: list[tuple[str, ...] | None] = []

    def fake_browser_login(
        environment_config: object,
        credentials: BrowserLoginCredentials,
        *,
        expected_return_paths: tuple[str, ...] | None = None,
    ) -> Session:
        assert environment_config is config
        assert credentials.username == "staging-primary@example.invalid"
        observed_return_paths.append(expected_return_paths)
        return Session(cookie_name="BA_SESSION", cookie_value="primary-cookie")

    monkeypatch.setattr(auth_module, "login_with_browser", fake_browser_login)

    session = acquire_browser_session(
        config,
        "primary",
        environ={
            "BA_PRIMARY_TEST_USERNAME": "staging-primary@example.invalid",
            "BA_PRIMARY_TEST_PASSWORD": "primary-password",
        },
    )

    assert session == Session(cookie_name="BA_SESSION", cookie_value="primary-cookie")
    assert observed_return_paths == [("/",)]


def test_expected_browser_return_paths_accept_configured_admin_destination() -> None:
    config = load_environment("local")

    paths = browser_login_module._resolve_expected_return_paths(
        config,
        expected_return_path=None,
        expected_return_paths=(
            config.browser_auth_contract.default_return_path,
            config.browser_auth_contract.admin_return_path,
        ),
    )

    assert paths == ("/", "/admin")


def test_expected_browser_return_paths_reject_conflicting_arguments() -> None:
    with pytest.raises(BrowserLoginError, match="cannot both be provided"):
        browser_login_module._resolve_expected_return_paths(
            load_environment("local"),
            expected_return_path="/",
            expected_return_paths=("/admin",),
        )


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


def test_supplied_sessions_load_all_roles_independently() -> None:
    config = load_environment("local")
    source = {
        "BA_PRIMARY_SESSION": "primary-cookie",
        "BA_SECONDARY_SESSION": "secondary-cookie",
        "BA_ADMIN_SESSION": "admin-cookie",
    }

    primary = load_supplied_session(config, "primary", environ=source)
    secondary = load_supplied_session(config, "secondary", environ=source)
    admin = load_supplied_session(config, "admin", environ=source)

    assert primary == Session(cookie_name="BA_SESSION", cookie_value="primary-cookie")
    assert secondary == Session(cookie_name="BA_SESSION", cookie_value="secondary-cookie")
    assert admin == Session(cookie_name="BA_SESSION", cookie_value="admin-cookie")


def test_staging_browser_admin_uses_explicit_admin_credential_names() -> None:
    config = load_environment("staging")
    observed: list[BrowserLoginCredentials] = []

    def login(
        environment_config: object,
        credentials: BrowserLoginCredentials,
    ) -> Session:
        assert environment_config is config
        observed.append(credentials)
        return Session(cookie_name="BA_SESSION", cookie_value="admin-cookie")

    session = acquire_browser_session(
        config,
        "admin",
        environ={
            "BA_ADMIN_TEST_USERNAME": "staging-admin@example.invalid",
            "BA_ADMIN_TEST_PASSWORD": "admin-password",
        },
        login=login,
    )

    assert observed == [BrowserLoginCredentials("staging-admin@example.invalid", "admin-password")]
    assert session == Session(cookie_name="BA_SESSION", cookie_value="admin-cookie")


def test_isolated_lifecycle_rejects_supplied_session_mode() -> None:
    config = with_session_mode(load_environment("local"), "supplied_sessions")

    with pytest.raises(AuthConfigurationError, match="cannot create a test-owned session"):
        load_existing_browser_credentials(config, "primary", environ={})


def test_isolated_lifecycle_loads_only_an_existing_local_identity(tmp_path: Path) -> None:
    credential_path = tmp_path / ".env.local"
    credential_path.write_text(
        "BA_PRIMARY_TEST_USERNAME=existing@test.com\n"
        "BA_PRIMARY_TEST_PASSWORD=existing-password\n"
        "BA_PRIMARY_TEST_ACCOUNT_STATE=complete\n",
        encoding="utf-8",
    )

    credentials = load_existing_browser_credentials(
        load_environment("local"),
        "primary",
        environ={},
        credential_path=credential_path,
    )

    assert credentials == BrowserLoginCredentials("existing@test.com", "existing-password")


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
    with pytest.raises(MissingAdminSessionError, match="does not configure"):
        load_supplied_session(config, "admin", environ={})


def test_supplied_session_requires_requested_cookie() -> None:
    config = load_environment("local")

    with pytest.raises(AuthConfigurationError, match="BA_PRIMARY_SESSION"):
        load_supplied_session(config, "primary", environ={})


def test_local_admin_promotion_message_uses_saved_slot_without_credentials() -> None:
    message = admin_promotion_message(
        load_environment("local"),
        "idp|stable-subject",
        environ={},
    )

    assert message == (
        "Please promote the test admin user to the ADMIN role in the database.\n"
        "Add ADMIN while retaining USER. The local account username is stored as "
        "BA_ADMIN_TEST_USERNAME in .env.local.\n"
        "Then rerun the same command."
    )
    assert "stable-subject" not in message


def test_external_admin_promotion_message_names_source_and_redacts_secrets() -> None:
    password = "secret-admin-password"
    username = "external-admin@example.invalid"
    message = admin_promotion_message(
        load_environment("local"),
        "idp|stable-subject",
        environ={
            "BA_ADMIN_TEST_USERNAME": username,
            "BA_ADMIN_TEST_PASSWORD": password,
        },
    )

    assert "BA_ADMIN_TEST_USERNAME" in message
    assert "BA_ADMIN_TEST_PASSWORD" in message
    assert "idp|stable-subject" in message
    assert username not in message
    assert password not in message


def test_supplied_admin_promotion_requires_a_refreshed_external_session() -> None:
    config = with_session_mode(load_environment("local"), "supplied_sessions")

    message = admin_promotion_message(config, "idp|stable-subject", environ={})

    assert "BA_ADMIN_SESSION" in message
    assert "idp|stable-subject" in message
    assert "refreshed session" in message


def test_secret_value_representations_are_redacted() -> None:
    credentials = BrowserLoginCredentials(username="user@example.test", password="secret-password")
    session = Session(cookie_name="BA_SESSION", cookie_value="secret-cookie")

    rendered = "\n".join((repr(credentials), repr(session)))

    assert "user@example.test" not in rendered
    assert "secret-password" not in rendered
    assert "secret-cookie" not in rendered
    assert "BA_SESSION" in rendered


def test_browser_login_failure_traceback_does_not_expose_original_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = BrowserLoginCredentials(username="user@example.test", password="secret-password")
    code = "secret-code"
    state = "secret-state"
    diagnostic = (
        f"navigation failed: https://app.example/login/oauth2/code/idp?code={code}&state={state}"
        f" username={credentials.username} password={credentials.password}"
    )

    def fail_playwright() -> None:
        raise PlaywrightError(diagnostic)

    monkeypatch.setattr(browser_login_module, "sync_playwright", fail_playwright)

    with pytest.raises(BrowserLoginError, match="browser login failed") as exc_info:
        browser_login_module.login_flow_with_browser(load_environment("local"), credentials)

    rendered = "".join(traceback.format_exception(exc_info.value))
    assert "browser login failed" in rendered
    assert "[REDACTED]" in rendered
    for secret in (credentials.username, credentials.password, code, state):
        assert secret not in rendered


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
                "userId": "usr-sensitive-target",
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
    assert "usr-sensitive-target" not in log_entry["response_body_prefix"]


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


def test_oauth_diagnostics_redact_query_values_and_location_header() -> None:
    url = (
        "https://app.example/login/oauth2/code/idp?code=secret-code&state=secret-state"
        "&returnUrl=%2Fsettings"
    )

    redacted_url = _redact_url(url)
    redacted_headers = _redact_headers(
        httpx.Headers({"location": "https://tenant.auth0.com/authorize?state=secret-state"})
    )

    assert "secret-code" not in redacted_url
    assert "secret-state" not in redacted_url
    assert "returnUrl=%2Fsettings" in redacted_url
    assert redacted_headers["location"] == "[REDACTED]"


def test_user_admin_diagnostics_redact_arbitrary_profiles_and_encoded_queries(
    tmp_path: Path,
) -> None:
    email = "real.person+phase4@corp.example"
    display_name = "Real Profile Name"
    subject = "idp|arbitrary-profile-subject"
    user_id = "usr_arbitrary_internal_id"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            headers={"content-type": "application/json"},
            json={
                "content": [
                    {
                        "id": user_id,
                        "idpSub": subject,
                        "email": email,
                        "displayName": display_name,
                        "status": "ACTIVE",
                    }
                ]
            },
        )

    artifact_path = tmp_path / "request-log.jsonl"
    with GatewayClient(
        load_environment("local"),
        artifact_path=artifact_path,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = client.api_request(
            "GET",
            f"/v1/users/{user_id}",
            params={"email": email, "displayName": display_name, "idpSub": subject},
        )

    log_entry = json.loads(artifact_path.read_text(encoding="utf-8"))
    rendered_log = json.dumps(log_entry, sort_keys=True)
    with pytest.raises(AssertionError) as exc_info:
        assert_status(response, httpx.codes.OK)
    rendered_failure = str(exc_info.value)

    for sensitive_value in (email, display_name, subject, user_id):
        assert sensitive_value not in rendered_log
        assert sensitive_value not in rendered_failure
    assert "real.person%2Bphase4%40corp.example" not in rendered_log
    assert rendered_log.count("REDACTED") >= 4


def test_session_gateway_user_diagnostics_redact_subject_and_name() -> None:
    subject = "idp|arbitrary-session-subject"
    display_name = "Arbitrary Session Profile"
    response = httpx.Response(
        500,
        request=httpx.Request("GET", "https://app.example/auth/v1/user"),
        headers={"content-type": "application/json"},
        json={
            "authenticated": True,
            "sub": subject,
            "name": display_name,
            "email": "session.profile@corp.example",
            "roles": ["USER"],
        },
    )

    context = redacted_response_context(response)

    assert subject not in context
    assert display_name not in context
    assert "session.profile@corp.example" not in context
    assert context.count("[REDACTED]") == 3


def test_user_profile_failure_is_redacted_from_junit_output(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        """
        import httpx

        from api_tests.assertions import assert_status


        def test_profile_failure():
            user_id = "usr_" + "junit_secret"
            subject = "idp|" + "junit-secret"
            email = "junit.person" + "@corp.example"
            display_name = "JUnit " + "Secret Name"
            response = httpx.Response(
                500,
                request=httpx.Request(
                    "GET",
                    "https://app.example/api/v1/users/" + user_id,
                    params={
                        "email": email,
                        "displayName": display_name,
                        "idpSub": subject,
                    },
                ),
                headers={"content-type": "application/json"},
                json={
                    "id": user_id,
                    "idpSub": subject,
                    "email": email,
                    "displayName": display_name,
                },
            )
            assert_status(response, httpx.codes.OK)
        """
    )
    junit_path = pytester.path / "profile-results.xml"

    result = pytester.runpytest("--junitxml", str(junit_path))

    result.assert_outcomes(failed=1)
    junit = junit_path.read_text(encoding="utf-8")
    for sensitive_value in (
        "usr_junit_secret",
        "idp|junit-secret",
        "junit.person@corp.example",
        "JUnit Secret Name",
    ):
        assert sensitive_value not in junit
    assert "REDACTED" in junit


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
