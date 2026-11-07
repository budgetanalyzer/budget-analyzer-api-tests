from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import assert_non_html_response
from api_tests.client import GatewayClient


@pytest.mark.gateway_routing
@pytest.mark.readonly
@pytest.mark.production_safe
def test_unknown_session_gateway_route_returns_non_html_not_found(
    unauthenticated_gateway_client: GatewayClient,
) -> None:
    response = unauthenticated_gateway_client.raw_request(
        "GET",
        "/auth/v1/__black_box_missing__",
    )

    assert response.status_code == httpx.codes.NOT_FOUND
    assert_non_html_response(response)


@pytest.mark.gateway_routing
@pytest.mark.mutation
def test_session_gateway_user_route_rejects_post(
    gateway_client: GatewayClient,
) -> None:
    response = gateway_client.raw_request("POST", "/auth/v1/user")

    assert response.status_code == httpx.codes.METHOD_NOT_ALLOWED
    assert_non_html_response(response)
