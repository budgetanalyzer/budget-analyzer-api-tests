from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import JsonObject, json_object_response
from api_tests.client import GatewayClient


@pytest.mark.readonly
@pytest.mark.production_safe
def test_openapi_docs_returns_json_contract(
    unauthenticated_gateway_client: GatewayClient,
) -> None:
    response = unauthenticated_gateway_client.raw_request(
        "GET",
        unauthenticated_gateway_client.config.openapi_path,
    )

    assert response.status_code == httpx.codes.OK
    assert response.headers["content-type"].lower().startswith("application/json")

    _assert_openapi_document_shape(json_object_response(response, httpx.codes.OK))


def _assert_openapi_document_shape(body: JsonObject) -> None:
    openapi_version = body.get("openapi")
    assert isinstance(openapi_version, str)
    assert openapi_version.startswith("3.")

    paths = body.get("paths")
    assert isinstance(paths, dict)
    assert paths
    for path, path_item in paths.items():
        assert isinstance(path, str)
        assert path.startswith("/")
        assert isinstance(path_item, dict)

    components = body.get("components")
    if components is not None:
        assert isinstance(components, dict)
