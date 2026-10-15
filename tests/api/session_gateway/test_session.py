from __future__ import annotations

import time

import httpx
import pytest

from api_tests.assertions import (
    assert_api_error,
    assert_cookie_cleared,
    assert_cookie_not_set,
    int_field,
    json_object_response,
    string_array_field,
    string_field,
)
from api_tests.client import GatewayClient
from api_tests.config import EnvironmentConfig
from api_tests.openapi import OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi
from api_tests.session_gateway_assertions import assert_authenticated_user_response

_OPERATION_ID = "getSessionStatus"
_PATH = "/auth/v1/session"
_MAX_EXPECTED_FUTURE_EXPIRY_SECONDS = 3_600


@pytest.mark.authentication
@pytest.mark.readonly
@pytest.mark.production_safe
@pytest.mark.openapi("getSessionStatus", contract="session_gateway")
@pytest.mark.session_gateway_case("heartbeat-unauthenticated")
def test_session_heartbeat_rejects_missing_session(
    unauthenticated_gateway_client: GatewayClient,
    env_config: EnvironmentConfig,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    response = unauthenticated_gateway_client.raw_request("GET", _PATH)

    assert_cookie_not_set(response, env_config.session_cookie_name)
    _assert_unauthorized(response, session_gateway_openapi_snapshot)


@pytest.mark.authentication
@pytest.mark.readonly
@pytest.mark.production_safe
@pytest.mark.openapi("getSessionStatus", contract="session_gateway")
@pytest.mark.session_gateway_case("heartbeat-invalid-session")
def test_session_heartbeat_rejects_and_clears_invalid_session(
    invalid_session_gateway_client: GatewayClient,
    env_config: EnvironmentConfig,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    response = invalid_session_gateway_client.raw_request("GET", _PATH)

    assert_cookie_cleared(response, env_config.session_cookie_name)
    _assert_unauthorized(response, session_gateway_openapi_snapshot)


@pytest.mark.mutation
@pytest.mark.openapi("getSessionStatus", contract="session_gateway")
@pytest.mark.openapi("getCurrentUser", contract="session_gateway")
@pytest.mark.session_gateway_case("heartbeat-active-session")
@pytest.mark.session_gateway_case("current-user-authenticated")
def test_session_heartbeat_refreshes_and_preserves_primary_session(
    gateway_client: GatewayClient,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    request_started_at = int(time.time())
    heartbeat = gateway_client.raw_request("GET", _PATH)
    response_received_at = int(time.time())

    assert_response_matches_openapi(
        heartbeat,
        _OPERATION_ID,
        httpx.codes.OK,
        session_gateway_openapi_snapshot,
    )
    heartbeat_body = json_object_response(heartbeat, httpx.codes.OK)
    string_field(heartbeat_body, "userId")
    heartbeat_roles = string_array_field(heartbeat_body, "roles")
    expires_at = int_field(heartbeat_body, "expiresAt")
    assert expires_at > request_started_at
    assert expires_at <= response_received_at + _MAX_EXPECTED_FUTURE_EXPIRY_SECONDS

    current_user = gateway_client.raw_request("GET", "/auth/v1/user")
    user_body = assert_authenticated_user_response(
        current_user,
        session_gateway_openapi_snapshot,
    )
    assert string_array_field(user_body, "roles") == heartbeat_roles


def _assert_unauthorized(
    response: httpx.Response,
    openapi_snapshot: OpenApiDocument,
) -> None:
    body = assert_api_error(
        response,
        httpx.codes.UNAUTHORIZED,
        error_type="UNAUTHORIZED",
    )
    assert string_field(body, "message") == "Authentication required"
    assert_response_matches_openapi(
        response,
        _OPERATION_ID,
        httpx.codes.UNAUTHORIZED,
        openapi_snapshot,
    )
