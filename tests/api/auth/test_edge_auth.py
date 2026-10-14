from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import assert_api_error, assert_non_html_response, string_field
from api_tests.client import GatewayClient
from api_tests.config import repository_root
from api_tests.openapi import (
    OpenApiDocument,
    OpenApiRequestCase,
    RequestSafetyMarker,
    build_operation_request_cases,
    execute_openapi_request_case,
    load_snapshot,
    unauthenticated_request_safety_markers,
)
from api_tests.schemas import assert_response_matches_openapi

_UNAUTHENTICATED_REQUEST_CASES = build_operation_request_cases(
    load_snapshot(repository_root() / "schemas" / "openapi.json"),
    required_response_statuses=(401, 403),
)

_SESSION_GATEWAY_MACHINE_PATHS = (
    pytest.param(
        "/auth/v1/user",
        id="user",
        marks=(pytest.mark.readonly, pytest.mark.production_safe),
    ),
    pytest.param(
        "/auth/v1/session",
        id="session",
        marks=(pytest.mark.readonly, pytest.mark.production_safe),
    ),
)


def _case_marks(case: OpenApiRequestCase) -> tuple[pytest.MarkDecorator, ...]:
    safety_markers: tuple[RequestSafetyMarker, ...] = unauthenticated_request_safety_markers(case)
    return (
        pytest.mark.authentication,
        *(getattr(pytest.mark, marker) for marker in safety_markers),
    )


@pytest.mark.parametrize(
    "request_case",
    [
        pytest.param(case, id=case.case_id, marks=_case_marks(case))
        for case in _UNAUTHENTICATED_REQUEST_CASES
    ],
)
def test_every_api_operation_requires_authentication(
    unauthenticated_gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
    request_case: OpenApiRequestCase,
) -> None:
    response = execute_openapi_request_case(unauthenticated_gateway_client, request_case)

    try:
        assert_response_matches_openapi(
            response,
            request_case.operation_id,
            httpx.codes.UNAUTHORIZED,
            openapi_snapshot,
        )
        assert_non_html_response(response)
        assert_api_error(
            response,
            httpx.codes.UNAUTHORIZED,
            error_type="UNAUTHORIZED",
        )
    except AssertionError as exc:
        raise AssertionError(
            f"unauthenticated {request_case.operation_id} request "
            f"({request_case.method} {request_case.path}) failed: {exc}"
        ) from exc


@pytest.mark.authentication
@pytest.mark.parametrize("path", _SESSION_GATEWAY_MACHINE_PATHS)
def test_session_gateway_machine_endpoint_requires_authentication(
    unauthenticated_gateway_client: GatewayClient,
    path: str,
) -> None:
    response = unauthenticated_gateway_client.raw_request("GET", path)

    media_type = response.headers.get("content-type", "").split(";", maxsplit=1)[0].lower()
    assert media_type == "application/json"
    body = assert_api_error(
        response,
        httpx.codes.UNAUTHORIZED,
        error_type="UNAUTHORIZED",
    )
    assert string_field(body, "message") == "Authentication required"
