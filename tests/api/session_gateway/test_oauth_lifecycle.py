from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest

from api_tests.assertions import assert_api_error, string_field
from api_tests.auth import AuthConfigurationError, load_existing_browser_credentials
from api_tests.browser_login import (
    BrowserLoginCredentials,
    BrowserLoginResult,
    login_flow_with_browser,
)
from api_tests.client import GatewayClient
from api_tests.config import EnvironmentConfig
from api_tests.openapi import OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi
from api_tests.session import Session
from api_tests.session_gateway_assertions import (
    assert_authenticated_user_response,
    assert_browser_login_contract,
    assert_controlled_callback_failure_redirect,
    assert_logout_redirect,
    assert_oauth_authorization_redirect,
)

_AUTHORIZATION_PATH = "/oauth2/authorization/idp"
_USER_PATH = "/auth/v1/user"
_HEARTBEAT_PATH = "/auth/v1/session"
_LOGOUT_PATH = "/logout"


@pytest.fixture
def browser_lifecycle_credentials(
    env_config: EnvironmentConfig,
    primary_session: Session,
) -> BrowserLoginCredentials:
    """Reuse the configured identity without replacing its session-scoped session."""
    del primary_session
    try:
        return load_existing_browser_credentials(env_config, "primary")
    except AuthConfigurationError as exc:
        pytest.skip(str(exc))


@pytest.mark.mutation
@pytest.mark.session_gateway_case("oauth-authorization-default-redirect")
@pytest.mark.session_gateway_case("oauth-callback-provider-rejection")
def test_oauth_authorization_redirect_and_provider_rejection_are_controlled(
    unauthenticated_gateway_client: GatewayClient,
    env_config: EnvironmentConfig,
) -> None:
    authorization = unauthenticated_gateway_client.raw_request("GET", _AUTHORIZATION_PATH)
    parsed = assert_oauth_authorization_redirect(authorization, env_config)

    callback_query = urlencode({"error": "access_denied", "state": parsed.state})
    callback = unauthenticated_gateway_client.raw_request(
        "GET",
        f"{env_config.browser_auth_contract.callback_path}?{callback_query}",
    )

    assert_controlled_callback_failure_redirect(callback)


@pytest.mark.mutation
@pytest.mark.session_gateway_case("oauth-callback-invalid-state")
def test_oauth_callback_rejects_invalid_state_without_provider_exchange(
    unauthenticated_gateway_client: GatewayClient,
    env_config: EnvironmentConfig,
) -> None:
    callback_query = urlencode({"code": "invalid", "state": "unknown"})
    response = unauthenticated_gateway_client.raw_request(
        "GET",
        f"{env_config.browser_auth_contract.callback_path}?{callback_query}",
    )

    assert_controlled_callback_failure_redirect(response)


@pytest.mark.mutation
@pytest.mark.destructive
@pytest.mark.openapi("logout", contract="session_gateway")
@pytest.mark.session_gateway_case("oauth-callback-success-default")
@pytest.mark.session_gateway_case("logout-active-session")
def test_oauth_default_callback_cookie_and_logout_lifecycle(
    env_config: EnvironmentConfig,
    browser_lifecycle_credentials: BrowserLoginCredentials,
    session_gateway_openapi_snapshot: OpenApiDocument,
    request_log_path: Path | None,
) -> None:
    _exercise_owned_lifecycle(
        env_config,
        browser_lifecycle_credentials,
        session_gateway_openapi_snapshot,
        request_log_path,
        return_url=None,
        expected_final_path=env_config.browser_auth_contract.default_return_path,
    )


@pytest.mark.mutation
@pytest.mark.destructive
@pytest.mark.openapi("logout", contract="session_gateway")
@pytest.mark.session_gateway_case("oauth-authorization-valid-return-url")
@pytest.mark.session_gateway_case("oauth-callback-success-return-url")
@pytest.mark.session_gateway_case("logout-active-session")
def test_oauth_safe_return_url_cookie_and_logout_lifecycle(
    env_config: EnvironmentConfig,
    browser_lifecycle_credentials: BrowserLoginCredentials,
    session_gateway_openapi_snapshot: OpenApiDocument,
    request_log_path: Path | None,
) -> None:
    return_path = env_config.browser_auth_contract.logout_return_path
    _exercise_owned_lifecycle(
        env_config,
        browser_lifecycle_credentials,
        session_gateway_openapi_snapshot,
        request_log_path,
        return_url=return_path,
        expected_final_path=return_path,
    )


@pytest.mark.mutation
@pytest.mark.destructive
@pytest.mark.openapi("logout", contract="session_gateway")
@pytest.mark.session_gateway_case("oauth-authorization-unsafe-return-url")
@pytest.mark.session_gateway_case("oauth-callback-success-default")
@pytest.mark.session_gateway_case("logout-active-session")
@pytest.mark.parametrize(
    "unsafe_return_url",
    ["https://attacker.example/after-login", "//attacker.example/after-login"],
    ids=["external-absolute", "protocol-relative"],
)
def test_oauth_unsafe_return_url_falls_back_before_logout(
    env_config: EnvironmentConfig,
    browser_lifecycle_credentials: BrowserLoginCredentials,
    session_gateway_openapi_snapshot: OpenApiDocument,
    request_log_path: Path | None,
    unsafe_return_url: str,
) -> None:
    _exercise_owned_lifecycle(
        env_config,
        browser_lifecycle_credentials,
        session_gateway_openapi_snapshot,
        request_log_path,
        return_url=unsafe_return_url,
        expected_final_path=env_config.browser_auth_contract.default_return_path,
    )


@pytest.mark.destructive
@pytest.mark.openapi("logout", contract="session_gateway")
@pytest.mark.session_gateway_case("logout-without-session")
def test_logout_without_cookie_is_idempotent_redirect_and_clear(
    unauthenticated_gateway_client: GatewayClient,
    env_config: EnvironmentConfig,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    response = unauthenticated_gateway_client.raw_request("GET", _LOGOUT_PATH)

    assert_response_matches_openapi(
        response,
        "logout",
        httpx.codes.FOUND,
        session_gateway_openapi_snapshot,
    )
    assert_logout_redirect(response, env_config)


def _exercise_owned_lifecycle(
    config: EnvironmentConfig,
    credentials: BrowserLoginCredentials,
    openapi_snapshot: OpenApiDocument,
    request_log_path: Path | None,
    *,
    return_url: str | None,
    expected_final_path: str,
) -> None:
    result: BrowserLoginResult | None = None
    logout_confirmed = False
    try:
        result = login_flow_with_browser(
            config,
            credentials,
            return_url=return_url,
            expected_return_path=expected_final_path,
        )
        assert_browser_login_contract(
            result,
            config,
            expected_final_path=expected_final_path,
        )
        with GatewayClient(
            config,
            artifact_path=request_log_path,
            session=result.session,
        ) as authenticated_client:
            current_user = authenticated_client.raw_request("GET", _USER_PATH)
            assert_authenticated_user_response(current_user, openapi_snapshot)
            logout = authenticated_client.raw_request("GET", _LOGOUT_PATH)

        assert_response_matches_openapi(
            logout,
            "logout",
            httpx.codes.FOUND,
            openapi_snapshot,
        )
        assert_logout_redirect(logout, config)
        logout_confirmed = True

        _assert_session_invalidated(
            config,
            result.session,
            openapi_snapshot,
            request_log_path,
        )
    finally:
        original_failure = sys.exception()
        if result is not None and not logout_confirmed:
            try:
                _cleanup_exact_owned_session(config, result.session, request_log_path)
            except Exception:
                if original_failure is None:
                    raise


def _assert_session_invalidated(
    config: EnvironmentConfig,
    session: Session,
    openapi_snapshot: OpenApiDocument,
    request_log_path: Path | None,
) -> None:
    for path, operation_id in (
        (_USER_PATH, "getCurrentUser"),
        (_HEARTBEAT_PATH, "getSessionStatus"),
    ):
        with GatewayClient(
            config,
            artifact_path=request_log_path,
            session=session,
        ) as fresh_client:
            response = fresh_client.raw_request("GET", path)
        body = assert_api_error(
            response,
            httpx.codes.UNAUTHORIZED,
            error_type="UNAUTHORIZED",
        )
        assert string_field(body, "message") == "Authentication required"
        assert_response_matches_openapi(
            response,
            operation_id,
            httpx.codes.UNAUTHORIZED,
            openapi_snapshot,
        )


def _cleanup_exact_owned_session(
    config: EnvironmentConfig,
    session: Session,
    request_log_path: Path | None,
) -> None:
    with GatewayClient(
        config,
        artifact_path=request_log_path,
        session=session,
    ) as cleanup_client:
        response = cleanup_client.raw_request("GET", _LOGOUT_PATH)
    if response.status_code != httpx.codes.FOUND:
        raise AssertionError("narrow cleanup logout did not return the expected redirect")
