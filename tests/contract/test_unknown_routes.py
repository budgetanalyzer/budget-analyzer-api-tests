from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import assert_non_html_response
from api_tests.client import GatewayClient


@pytest.mark.readonly
@pytest.mark.production_safe
def test_unknown_api_route_fails_closed_without_spa_fallback(
    unauthenticated_gateway_client: GatewayClient,
) -> None:
    response = unauthenticated_gateway_client.api_request(
        "GET",
        "/v1/__openapi_black_box_missing__",
    )

    assert response.status_code == httpx.codes.UNAUTHORIZED
    assert_non_html_response(response)
