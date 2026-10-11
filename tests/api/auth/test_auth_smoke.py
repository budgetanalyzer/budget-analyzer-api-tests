import pytest

from api_tests.assertions import assert_status
from api_tests.client import GatewayClient


@pytest.mark.auth_smoke
@pytest.mark.readonly
def test_acquired_session_reaches_session_gateway(gateway_client: GatewayClient) -> None:
    response = gateway_client.raw_request("GET", "/auth/v1/user")

    assert_status(response, 200)


@pytest.mark.secondary_auth_smoke
@pytest.mark.readonly
def test_secondary_acquired_session_reaches_session_gateway(
    secondary_gateway_client: GatewayClient,
) -> None:
    response = secondary_gateway_client.raw_request("GET", "/auth/v1/user")

    assert_status(response, 200)
