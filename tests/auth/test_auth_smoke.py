from __future__ import annotations

import httpx
import pytest

from api_tests.client import GatewayClient
from api_tests.config import EnvironmentConfig
from api_tests.session import SessionContext


@pytest.mark.auth_smoke
@pytest.mark.readonly
def test_acquired_session_reaches_session_gateway(
    env_config: EnvironmentConfig,
    session_context: SessionContext,
) -> None:
    with GatewayClient(env_config, session_context=session_context) as gateway_client:
        response = gateway_client.raw_request("GET", "/auth/v1/user")

    assert response.status_code == httpx.codes.OK
