from __future__ import annotations

import httpx
import pytest

from api_tests.client import GatewayClient
from api_tests.config import EnvironmentConfig


@pytest.mark.readonly
@pytest.mark.production_safe
def test_openapi_docs_returns_json_contract(
    env_config: EnvironmentConfig,
    gateway_client: GatewayClient,
) -> None:
    response = gateway_client.raw_request("GET", env_config.openapi_path)

    assert response.status_code == httpx.codes.OK
    assert response.headers["content-type"].lower().startswith("application/json")

    body = response.json()
    assert isinstance(body.get("openapi"), str)
    assert isinstance(body.get("paths"), dict)
