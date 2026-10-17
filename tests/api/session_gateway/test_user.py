from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import (
    assert_api_error,
    string_field,
)
from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi
from api_tests.session_gateway_assertions import (
    AuthenticatedIdentity,
    assert_authenticated_user_response,
    authenticated_identity,
)

_OPERATION_ID = "getCurrentUser"
_PATH = "/auth/v1/user"


@pytest.mark.authentication
@pytest.mark.readonly
@pytest.mark.production_safe
@pytest.mark.openapi("getCurrentUser", contract="session_gateway")
@pytest.mark.session_gateway_case("current-user-unauthenticated")
def test_current_user_rejects_missing_session(
    unauthenticated_gateway_client: GatewayClient,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    response = unauthenticated_gateway_client.raw_request("GET", _PATH)

    _assert_unauthorized(response, session_gateway_openapi_snapshot)


@pytest.mark.authentication
@pytest.mark.readonly
@pytest.mark.production_safe
@pytest.mark.openapi("getCurrentUser", contract="session_gateway")
@pytest.mark.session_gateway_case("current-user-unauthenticated")
def test_current_user_rejects_invalid_session(
    invalid_session_gateway_client: GatewayClient,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    response = invalid_session_gateway_client.raw_request("GET", _PATH)

    _assert_unauthorized(response, session_gateway_openapi_snapshot)


@pytest.mark.auth_smoke
@pytest.mark.readonly
@pytest.mark.production_safe
@pytest.mark.openapi("getCurrentUser", contract="session_gateway")
@pytest.mark.session_gateway_case("current-user-authenticated")
def test_acquired_session_reaches_session_gateway(
    gateway_client: GatewayClient,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.raw_request("GET", _PATH)

    assert_authenticated_user_response(response, session_gateway_openapi_snapshot)


@pytest.mark.secondary_auth_smoke
@pytest.mark.readonly
def test_secondary_acquired_session_reaches_session_gateway(
    secondary_gateway_client: GatewayClient,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    """Keep independent-session smoke evidence without duplicating contract accounting."""
    response = secondary_gateway_client.raw_request("GET", _PATH)

    assert_authenticated_user_response(response, session_gateway_openapi_snapshot)


@pytest.mark.admin
@pytest.mark.admin_auth_smoke
@pytest.mark.readonly
def test_admin_acquired_session_has_required_authorization(
    admin_gateway_client: GatewayClient,
    admin_identity: AuthenticatedIdentity,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    """Keep admin readiness visible without duplicating operation-marker accounting."""
    response = admin_gateway_client.raw_request("GET", _PATH)

    assert authenticated_identity(response, session_gateway_openapi_snapshot) == admin_identity


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
