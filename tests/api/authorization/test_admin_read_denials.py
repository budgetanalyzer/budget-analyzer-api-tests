from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest

from api_tests.assertions import assert_api_error, assert_non_html_response
from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi
from api_tests.session_gateway_assertions import AuthenticatedIdentity


@pytest.mark.forbidden_response("searchTransactions")
@pytest.mark.authorization
@pytest.mark.readonly
@pytest.mark.production_safe
def test_normal_user_cannot_search_transactions_across_users(
    gateway_client: GatewayClient,
    primary_identity: AuthenticatedIdentity,
    openapi_snapshot: OpenApiDocument,
) -> None:
    del primary_identity
    response = gateway_client.api_request("GET", "/v1/transactions/search")

    _assert_forbidden(response, "searchTransactions", openapi_snapshot)


@pytest.mark.forbidden_response("countTransactionsAcrossUsers")
@pytest.mark.authorization
@pytest.mark.readonly
@pytest.mark.production_safe
def test_normal_user_cannot_count_transactions_across_users(
    gateway_client: GatewayClient,
    primary_identity: AuthenticatedIdentity,
    openapi_snapshot: OpenApiDocument,
) -> None:
    del primary_identity
    response = gateway_client.api_request("GET", "/v1/transactions/search/count")

    _assert_forbidden(response, "countTransactionsAcrossUsers", openapi_snapshot)


@pytest.mark.forbidden_response("getUsers")
@pytest.mark.authorization
@pytest.mark.readonly
@pytest.mark.production_safe
def test_normal_user_cannot_list_users(
    gateway_client: GatewayClient,
    primary_identity: AuthenticatedIdentity,
    openapi_snapshot: OpenApiDocument,
) -> None:
    del primary_identity
    response = gateway_client.api_request("GET", "/v1/users")

    _assert_forbidden(response, "getUsers", openapi_snapshot)


@pytest.mark.forbidden_response("getUser")
@pytest.mark.authorization
@pytest.mark.readonly
@pytest.mark.production_safe
def test_normal_user_cannot_read_user_detail(
    gateway_client: GatewayClient,
    primary_identity: AuthenticatedIdentity,
    openapi_snapshot: OpenApiDocument,
) -> None:
    known_test_identity = quote(primary_identity.subject, safe="")
    response = gateway_client.api_request("GET", f"/v1/users/{known_test_identity}")

    _assert_forbidden(response, "getUser", openapi_snapshot)


def _assert_forbidden(
    response: httpx.Response,
    operation_id: str,
    openapi_snapshot: OpenApiDocument,
) -> None:
    assert_response_matches_openapi(
        response,
        operation_id,
        httpx.codes.FORBIDDEN,
        openapi_snapshot,
    )
    assert_non_html_response(response)
    assert_api_error(response, httpx.codes.FORBIDDEN, error_type="FORBIDDEN")
