from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import assert_non_html_response
from api_tests.client import GatewayClient


@pytest.mark.authorization
@pytest.mark.readonly
@pytest.mark.production_safe
def test_unauthenticated_api_request_fails_at_gateway_boundary(
    unauthenticated_gateway_client: GatewayClient,
) -> None:
    response = unauthenticated_gateway_client.api_request("GET", "/v1/currencies")

    assert response.status_code == httpx.codes.UNAUTHORIZED
    assert_non_html_response(response)
