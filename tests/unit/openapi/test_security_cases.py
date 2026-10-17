from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import quote, unquote

import httpx
import pytest

from api_tests.client import (
    GatewayClient,
    GatewayRequestPacing,
    RequestPacer,
)
from api_tests.config import load_environment, repository_root
from api_tests.coverage import (
    MarkerCounts,
    build_forbidden_response_coverage,
    collect_forbidden_response_markers_from_tests,
)
from api_tests.openapi import (
    ForbiddenResponseDeferral,
    JsonValue,
    OpenApiDocument,
    OpenApiRequestCase,
    build_operation_request_cases,
    execute_openapi_request_case,
    iter_operations,
    load_forbidden_response_deferrals,
    load_snapshot,
    operation_response_schema,
    operations_documenting_response,
    unauthenticated_request_safety_markers,
)

API_ERROR_SCHEMA: dict[str, JsonValue] = {"$ref": "#/components/schemas/ApiErrorResponse"}
DESTRUCTIVE_OPERATION_IDS = {
    "batchImportTransactions",
    "bulkDeleteTransactions",
    "deactivateUser",
    "deleteTransaction",
    "deleteView",
    "hideFormat",
    "importLatestExchangeRates",
    "updateViewTransactions",
}


def _path_parameter(
    name: str,
    schema_type: str,
    *,
    example: JsonValue = None,
) -> JsonValue:
    parameter: dict[str, JsonValue] = {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": schema_type},
    }
    if example is not None:
        parameter["example"] = example
    return parameter


def test_snapshot_has_one_stable_security_case_for_every_operation() -> None:
    document = _snapshot()
    operations = tuple(iter_operations(document))
    cases = build_operation_request_cases(
        document,
        required_response_statuses=(401, 403),
    )

    assert len(operations) == 40
    assert len(cases) == len(operations)
    assert len({case.operation_id for case in cases}) == len(cases)
    assert [case.case_id for case in cases] == [case.operation_id for case in cases]
    assert {case.operation_id for case in cases} == {
        operation.operation_id for operation in operations
    }
    assert {
        operation.operation_id for operation in operations_documenting_response(document, 401)
    } == {operation.operation_id for operation in operations}
    assert {
        operation.operation_id for operation in operations_documenting_response(document, 403)
    } == {operation.operation_id for operation in operations}

    for operation in operations:
        assert operation_response_schema(document, operation.operation_id, 401) == API_ERROR_SCHEMA
        assert operation_response_schema(document, operation.operation_id, 403) == API_ERROR_SCHEMA
    for case in cases:
        assert "{" not in case.path
        assert "}" not in case.path
        assert all(quote(unquote(segment), safe="") == segment for segment in case.path.split("/"))


def test_snapshot_security_case_methods_paths_and_safety_markers_are_deterministic() -> None:
    cases = build_operation_request_cases(_snapshot(), required_response_statuses=(401, 403))

    assert all(case.method == "GET" for case in cases if "readonly" in _markers(case))
    assert all(
        _markers(case) == ("readonly", "production_safe") for case in cases if case.method == "GET"
    )
    assert all("mutation" in _markers(case) for case in cases if case.method != "GET")
    assert {
        case.operation_id for case in cases if "destructive" in _markers(case)
    } == DESTRUCTIVE_OPERATION_IDS
    assert {(case.operation_id, case.method, case.path) for case in cases} >= {
        ("getAll", "GET", "/v1/currencies"),
        ("createTransaction", "POST", "/v1/transactions"),
        ("updateTransaction", "PATCH", "/v1/transactions/1"),
        ("deleteView", "DELETE", "/v1/views/00000000-0000-4000-8000-000000000001"),
    }


def test_snapshot_security_cases_compose_bodyless_sessionless_api_requests(
    tmp_path: Path,
) -> None:
    cases = build_operation_request_cases(_snapshot(), required_response_statuses=(401, 403))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            httpx.codes.UNAUTHORIZED,
            json={"type": "UNAUTHORIZED", "message": "Authentication required"},
        )

    with GatewayClient(
        load_environment("local"),
        artifact_path=tmp_path / "request-log.jsonl",
        transport=httpx.MockTransport(handler),
        request_pacing=GatewayRequestPacing(
            login_initiation_pacer=RequestPacer(requests_per_second=1_000_000),
            login_completion_pacer=RequestPacer(requests_per_second=1_000_000),
            session_operations_pacer=RequestPacer(requests_per_second=1_000_000),
            api_requests_pacer=RequestPacer(requests_per_second=1_000_000),
        ),
    ) as client:
        for case in cases:
            execute_openapi_request_case(client, case)

    assert [(request.method, request.url.path) for request in requests] == [
        (case.method, f"/api{case.path}") for case in cases
    ]
    assert all(request.url.query == b"" for request in requests)
    assert all(request.content == b"" for request in requests)
    assert all("authorization" not in request.headers for request in requests)
    assert all("cookie" not in request.headers for request in requests)


def test_snapshot_path_cases_prefer_examples_then_use_narrow_fallbacks() -> None:
    cases = {case.operation_id: case for case in build_operation_request_cases(_snapshot())}

    assert cases["getFormat"].path == "/v1/statement-formats/123"
    assert cases["getUser"].path == "/v1/users/usr_abc123"
    assert cases["getById"].path == "/v1/currencies/1"
    assert cases["getView"].path == ("/v1/views/00000000-0000-4000-8000-000000000001")


def test_path_materialization_url_quotes_parameter_example() -> None:
    document = _document(
        path="/v1/widgets/{widgetId}",
        parameters=[_path_parameter("widgetId", "string", example="primary / widget")],
    )

    case = build_operation_request_cases(document)[0]

    assert case.path == "/v1/widgets/primary%20%2F%20widget"


def test_security_cases_reject_missing_documented_response() -> None:
    document = _document(path="/v1/widgets", responses={"403": _error_response()})

    with pytest.raises(
        ValueError,
        match=r"OpenAPI operation 'getWidget'.*is missing documented 401 response",
    ):
        build_operation_request_cases(document, required_response_statuses=(401, 403))


def test_security_cases_reject_response_without_json_schema() -> None:
    document = _document(
        path="/v1/widgets",
        responses={"401": {"description": "Unauthorized"}},
    )

    with pytest.raises(
        ValueError,
        match=r"OpenAPI operation 'getWidget'.*response 401 is missing application/json content",
    ):
        build_operation_request_cases(document, required_response_statuses=(401,))


@pytest.mark.parametrize(
    ("path", "parameters", "message"),
    [
        (
            "/v1/widgets/{widgetId}",
            [],
            "path parameter 'widgetId' is missing a declaration",
        ),
        (
            "/v1/widgets/{widgetId}/{widgetId}",
            [_path_parameter("widgetId", "integer")],
            "duplicate path parameter 'widgetId'",
        ),
        (
            "/v1/widgets/{widgetId}",
            [_path_parameter("otherId", "integer")],
            "path parameter 'widgetId' is missing a declaration",
        ),
        (
            "/v1/widgets",
            [_path_parameter("widgetId", "integer")],
            "path parameter 'widgetId' does not match the path template",
        ),
        (
            "/v1/widgets/{widgetId}",
            [_path_parameter("widgetId", "number")],
            "path parameter 'widgetId' has unsupported schema type/format: number/None",
        ),
        (
            "/v1/widgets/{widgetId",
            [_path_parameter("widgetId", "integer")],
            "has unsupported path template",
        ),
    ],
)
def test_path_materialization_rejects_unsupported_templates(
    path: str,
    parameters: list[JsonValue],
    message: str,
) -> None:
    document = _document(path=path, parameters=parameters)

    with pytest.raises(ValueError, match=message):
        build_operation_request_cases(document)


def test_forbidden_response_inventory_partitions_covered_and_deferred_cases() -> None:
    document = _snapshot()
    deferrals = _deferrals()
    coverage = build_forbidden_response_coverage(
        operations_documenting_response(document, 403),
        deferrals,
        collect_forbidden_response_markers_from_tests(repository_root() / "tests"),
    )

    assert len(coverage) == 40
    assert {row.operation_id for row in coverage if row.status == "covered"} == {
        "countTransactionsAcrossUsers",
        "deactivateUser",
        "getUser",
        "getUsers",
        "searchTransactions",
    }
    assert sum(row.status == "deferred" for row in coverage) == 35
    assert {
        row.operation_id
        for row in coverage
        if row.deferral_reason == "global_mutation_not_authorized"
    } == {"create", "importLatestExchangeRates", "update"}


@pytest.mark.parametrize(
    ("field", "value", "manifest_value", "openapi_value"),
    [
        ("method", "POST", "POST /v1/currencies", "GET /v1/currencies"),
        ("path", "/v2/currencies", "GET /v2/currencies", "GET /v1/currencies"),
    ],
)
def test_forbidden_response_manifest_rejects_stale_method_or_path(
    field: str,
    value: str,
    manifest_value: str,
    openapi_value: str,
) -> None:
    deferrals = list(_deferrals())
    index = next(
        index for index, deferral in enumerate(deferrals) if deferral.operation_id == "getAll"
    )
    if field == "method":
        deferrals[index] = replace(deferrals[index], method="POST")
    else:
        deferrals[index] = replace(deferrals[index], path=value)

    with pytest.raises(
        ValueError,
        match=(
            "stale forbidden-response method/path for getAll: manifest has "
            f"{manifest_value}, OpenAPI has {openapi_value}"
        ),
    ):
        build_forbidden_response_coverage(
            operations_documenting_response(_snapshot(), 403),
            deferrals,
            collect_forbidden_response_markers_from_tests(repository_root() / "tests"),
        )


def test_forbidden_response_inventory_rejects_partition_gap() -> None:
    deferrals = tuple(
        deferral for deferral in _deferrals() if deferral.operation_id != "getExchangeRates"
    )

    with pytest.raises(
        ValueError,
        match=(
            "documented 403 responses have neither executable evidence nor deferrals: "
            "getExchangeRates"
        ),
    ):
        build_forbidden_response_coverage(
            operations_documenting_response(_snapshot(), 403),
            deferrals,
            collect_forbidden_response_markers_from_tests(repository_root() / "tests"),
        )


def test_forbidden_response_inventory_rejects_deferral_and_executable_overlap() -> None:
    deferrals = list(_deferrals())
    deferrals.append(
        ForbiddenResponseDeferral(
            operation_id="getUsers",
            method="GET",
            path="/v1/users",
            reason="no_restricted_normal_user_scenario",
        )
    )

    with pytest.raises(
        ValueError,
        match="both executable evidence and deferrals: getUsers",
    ):
        build_forbidden_response_coverage(
            operations_documenting_response(_snapshot(), 403),
            deferrals,
            collect_forbidden_response_markers_from_tests(repository_root() / "tests"),
        )


def test_forbidden_response_inventory_rejects_duplicate_deferral() -> None:
    deferrals = (*_deferrals(), _deferrals()[0])

    with pytest.raises(ValueError, match="duplicate forbidden-response deferral operationId"):
        build_forbidden_response_coverage(
            operations_documenting_response(_snapshot(), 403),
            deferrals,
            collect_forbidden_response_markers_from_tests(repository_root() / "tests"),
        )


def test_forbidden_response_inventory_rejects_stale_deferral() -> None:
    deferrals = (
        *_deferrals(),
        ForbiddenResponseDeferral(
            operation_id="removedOperation",
            method="GET",
            path="/v1/removed",
            reason="no_restricted_normal_user_scenario",
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            "stale forbidden-response deferral is absent from documented OpenAPI 403 "
            "responses: removedOperation"
        ),
    ):
        build_forbidden_response_coverage(
            operations_documenting_response(_snapshot(), 403),
            deferrals,
            collect_forbidden_response_markers_from_tests(repository_root() / "tests"),
        )


def test_forbidden_response_inventory_rejects_unknown_marker() -> None:
    with pytest.raises(
        ValueError,
        match=r"unrecognized pytest\.mark\.forbidden_response operations: removedOperation",
    ):
        build_forbidden_response_coverage(
            operations_documenting_response(_snapshot(), 403),
            _deferrals(),
            {"removedOperation": MarkerCounts(marker_count=1)},
        )


def test_forbidden_response_inventory_rejects_placeholder_only_evidence() -> None:
    with pytest.raises(
        ValueError,
        match="forbidden-response evidence has only placeholder tests: getUsers",
    ):
        build_forbidden_response_coverage(
            operations_documenting_response(_snapshot(), 403),
            _deferrals(),
            {
                "getUsers": MarkerCounts(marker_count=1, placeholder_marker_count=1),
            },
        )


def _snapshot() -> OpenApiDocument:
    return load_snapshot(repository_root() / "schemas" / "openapi.json")


def _deferrals() -> tuple[ForbiddenResponseDeferral, ...]:
    return load_forbidden_response_deferrals(
        repository_root() / "schemas" / "deferred-forbidden-responses.yaml"
    )


def _document(
    *,
    path: str,
    parameters: list[JsonValue] | None = None,
    responses: dict[str, JsonValue] | None = None,
) -> OpenApiDocument:
    return {
        "paths": {
            path: {
                "get": {
                    "operationId": "getWidget",
                    "parameters": parameters or [],
                    "responses": responses or {},
                }
            }
        }
    }


def _error_response() -> JsonValue:
    return {
        "description": "Error",
        "content": {"application/json": {"schema": API_ERROR_SCHEMA}},
    }


def _markers(case: OpenApiRequestCase) -> tuple[str, ...]:
    return unauthenticated_request_safety_markers(case)
