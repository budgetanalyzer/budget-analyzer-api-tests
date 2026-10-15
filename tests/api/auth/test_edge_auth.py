from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import assert_api_error, assert_non_html_response
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
