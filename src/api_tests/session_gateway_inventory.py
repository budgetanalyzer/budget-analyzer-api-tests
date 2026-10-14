from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

import yaml

from api_tests.openapi import (
    HttpMethod,
    JsonValue,
    OpenApiDocument,
    OpenApiOperation,
    iter_operations,
)

type RouteOwner = Literal["controller", "spring_security", "actuator", "springdoc"]
type RouteExposure = Literal[
    "public", "mesh_internal", "deployment_operational", "development_only"
]
type InventoryStatus = Literal["covered", "deferred", "untestable"]

_OWNERS = frozenset({"controller", "spring_security", "actuator", "springdoc"})
_EXPOSURES = frozenset({"public", "mesh_internal", "deployment_operational", "development_only"})
_STATUSES = frozenset({"covered", "deferred", "untestable"})
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class SessionGatewayCase:
    case_id: str
    behavior: str
    status: InventoryStatus
    reason: str | None = None
    prerequisite: str | None = None
    boundary: str | None = None


@dataclass(frozen=True, slots=True)
class SessionGatewayRoute:
    method: HttpMethod
    path: str
    owner: RouteOwner
    exposure: RouteExposure
    openapi_operation_id: str | None
    cases: tuple[SessionGatewayCase, ...]


@dataclass(frozen=True, slots=True)
class SessionGatewayCaseCoverage:
    contract: Literal["session_gateway"]
    case_id: str
    method: HttpMethod
    path: str
    owner: RouteOwner
    exposure: RouteExposure
    openapi_operation_id: str | None
    behavior: str
    status: InventoryStatus
    test_marker_count: int
    reason: str | None
    prerequisite: str | None
    boundary: str | None


def load_session_gateway_inventory(path: Path) -> tuple[SessionGatewayRoute, ...]:
    with path.open("r", encoding="utf-8") as inventory_file:
        raw_inventory = yaml.safe_load(inventory_file)
    if not isinstance(raw_inventory, dict):
        raise ValueError(f"Session Gateway inventory must contain a YAML mapping: {path}")
    raw_routes = raw_inventory.get("routes")
    if not isinstance(raw_routes, list):
        raise ValueError(f"Session Gateway inventory must contain list field 'routes': {path}")

    routes: list[SessionGatewayRoute] = []
    seen_case_ids: set[str] = set()
    for route_index, raw_route in enumerate(raw_routes, start=1):
        context = f"Session Gateway route #{route_index}"
        route = _load_route(raw_route, context=context)
        for case in route.cases:
            if case.case_id in seen_case_ids:
                raise ValueError(f"duplicate Session Gateway case id: {case.case_id}")
            seen_case_ids.add(case.case_id)
        routes.append(route)
    return tuple(routes)


def validate_session_gateway_inventory(
    routes: Iterable[SessionGatewayRoute],
    openapi_doc: OpenApiDocument,
    case_marker_counts: Mapping[str, int] | None = None,
) -> tuple[SessionGatewayCaseCoverage, ...]:
    route_list = tuple(routes)
    operations = tuple(iter_operations(openapi_doc, contract="session_gateway"))
    operations_by_id = {operation.operation_id: operation for operation in operations}
    seen_operation_ids: set[str] = set()
    seen_route_keys: set[tuple[HttpMethod, str]] = set()
    marker_counts = case_marker_counts or {}
    known_cases = {case.case_id: case for route in route_list for case in route.cases}
    unknown_markers = sorted(set(marker_counts) - known_cases.keys())
    if unknown_markers:
        raise ValueError(
            "unrecognized pytest.mark.session_gateway_case ids: " + ", ".join(unknown_markers)
        )
    noncovered_markers = sorted(
        case_id for case_id in marker_counts if known_cases[case_id].status != "covered"
    )
    if noncovered_markers:
        raise ValueError(
            "Session Gateway case markers may reference only covered cases: "
            + ", ".join(noncovered_markers)
        )
    rows: list[SessionGatewayCaseCoverage] = []

    for route in route_list:
        route_key = (route.method, route.path)
        if route_key in seen_route_keys:
            raise ValueError(f"duplicate Session Gateway route: {route.method} {route.path}")
        seen_route_keys.add(route_key)
        if route.openapi_operation_id is not None:
            operation = operations_by_id.get(route.openapi_operation_id)
            if operation is None:
                raise ValueError(
                    "inventory OpenAPI operation is absent from Session Gateway snapshot: "
                    f"{route.openapi_operation_id} ({route.method} {route.path})"
                )
            _validate_route_matches_operation(route, operation)
            if operation.operation_id in seen_operation_ids:
                raise ValueError(
                    f"duplicate inventory OpenAPI operation id: {operation.operation_id}"
                )
            seen_operation_ids.add(operation.operation_id)

        for case in route.cases:
            rows.append(
                SessionGatewayCaseCoverage(
                    contract="session_gateway",
                    case_id=case.case_id,
                    method=route.method,
                    path=route.path,
                    owner=route.owner,
                    exposure=route.exposure,
                    openapi_operation_id=route.openapi_operation_id,
                    behavior=case.behavior,
                    status=case.status,
                    test_marker_count=marker_counts.get(case.case_id, 0),
                    reason=case.reason,
                    prerequisite=case.prerequisite,
                    boundary=case.boundary,
                )
            )

    missing_operation_ids = sorted(operations_by_id.keys() - seen_operation_ids)
    if missing_operation_ids:
        raise ValueError(
            "Session Gateway snapshot operations missing from inventory: "
            + ", ".join(missing_operation_ids)
        )
    return tuple(sorted(rows, key=lambda row: (row.path, row.method, row.case_id)))


def validate_session_gateway_contract(openapi_doc: OpenApiDocument) -> None:
    operations = {
        operation.operation_id: operation
        for operation in iter_operations(openapi_doc, contract="session_gateway")
    }
    logout = operations.get("logout")
    if logout is None or logout.method != "GET" or logout.path != "/logout":
        raise ValueError("Session Gateway snapshot is missing logout GET /logout")

    paths = _mapping(openapi_doc.get("paths"), "Session Gateway OpenAPI paths")
    logout_path = _mapping(paths.get("/logout"), "Session Gateway /logout path item")
    logout_operation = _mapping(logout_path.get("get"), "Session Gateway GET /logout operation")
    responses = _mapping(logout_operation.get("responses"), "Session Gateway logout responses")
    if "302" not in responses:
        raise ValueError("Session Gateway logout must document response 302")
    if "200" in responses:
        raise ValueError("Session Gateway logout must not document stale response 200")
    response = _mapping(responses.get("302"), "Session Gateway logout response 302")
    headers = _mapping(response.get("headers"), "Session Gateway logout response 302 headers")
    location = _mapping(headers.get("Location"), "Session Gateway logout Location header")
    schema = _mapping(location.get("schema"), "Session Gateway logout Location header schema")
    if schema.get("type") != "string" or schema.get("format") != "uri":
        raise ValueError(
            "Session Gateway logout Location header must use a string schema with uri format"
        )


def write_session_gateway_coverage_artifact(
    path: Path,
    coverage: Iterable[SessionGatewayCaseCoverage],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(row) for row in coverage], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_route(raw_route: object, *, context: str) -> SessionGatewayRoute:
    if not isinstance(raw_route, dict):
        raise ValueError(f"{context} must be a mapping")
    method = raw_route.get("method")
    path = raw_route.get("path")
    owner = raw_route.get("owner")
    exposure = raw_route.get("exposure")
    operation_id = raw_route.get("openapi_operation_id")
    raw_cases = raw_route.get("cases")
    if method not in _METHODS:
        raise ValueError(f"{context} has unsupported method: {method}")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"{context} has invalid path")
    if owner not in _OWNERS:
        raise ValueError(f"{context} has unsupported owner: {owner}")
    if exposure not in _EXPOSURES:
        raise ValueError(f"{context} has unsupported exposure: {exposure}")
    if operation_id is not None and (not isinstance(operation_id, str) or not operation_id):
        raise ValueError(f"{context} has invalid openapi_operation_id")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError(f"{context} must contain a non-empty cases list")
    cases = tuple(
        _load_case(raw_case, context=f"{context} case #{index}")
        for index, raw_case in enumerate(raw_cases, start=1)
    )
    return SessionGatewayRoute(
        method=cast(HttpMethod, method),
        path=path,
        owner=cast(RouteOwner, owner),
        exposure=cast(RouteExposure, exposure),
        openapi_operation_id=operation_id,
        cases=cases,
    )


def _load_case(raw_case: object, *, context: str) -> SessionGatewayCase:
    if not isinstance(raw_case, dict):
        raise ValueError(f"{context} must be a mapping")
    case_id = raw_case.get("id")
    behavior = raw_case.get("behavior")
    status = raw_case.get("status")
    reason = raw_case.get("reason")
    prerequisite = raw_case.get("prerequisite")
    boundary = raw_case.get("boundary")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError(f"{context} is missing id")
    if not isinstance(behavior, str) or not behavior:
        raise ValueError(f"Session Gateway case {case_id} is missing behavior")
    if status not in _STATUSES:
        raise ValueError(f"Session Gateway case {case_id} has unsupported status: {status}")
    for field_name, value in (
        ("reason", reason),
        ("prerequisite", prerequisite),
        ("boundary", boundary),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"Session Gateway case {case_id} has invalid {field_name}")
    _validate_classification(
        case_id,
        cast(InventoryStatus, status),
        reason=cast(str | None, reason),
        prerequisite=cast(str | None, prerequisite),
        boundary=cast(str | None, boundary),
    )
    return SessionGatewayCase(
        case_id=case_id,
        behavior=behavior,
        status=cast(InventoryStatus, status),
        reason=cast(str | None, reason),
        prerequisite=cast(str | None, prerequisite),
        boundary=cast(str | None, boundary),
    )


def _validate_classification(
    case_id: str,
    status: InventoryStatus,
    *,
    reason: str | None,
    prerequisite: str | None,
    boundary: str | None,
) -> None:
    if status == "covered":
        if reason is not None or prerequisite is not None or boundary is not None:
            raise ValueError(f"covered Session Gateway case {case_id} must not declare deferral")
    elif status == "deferred":
        if reason is None or prerequisite is None:
            raise ValueError(
                f"deferred Session Gateway case {case_id} requires reason and prerequisite"
            )
        if boundary is not None:
            raise ValueError(f"deferred Session Gateway case {case_id} must not declare boundary")
    else:
        if reason is None or boundary is None:
            raise ValueError(
                f"untestable Session Gateway case {case_id} requires reason and boundary"
            )
        if prerequisite is not None:
            raise ValueError(
                f"untestable Session Gateway case {case_id} must not declare prerequisite"
            )


def _validate_route_matches_operation(
    route: SessionGatewayRoute,
    operation: OpenApiOperation,
) -> None:
    if route.method != operation.method or route.path != operation.path:
        raise ValueError(
            f"stale Session Gateway inventory route for {operation.operation_id}: inventory has "
            f"{route.method} {route.path}, snapshot has {operation.method} {operation.path}"
        )


def _mapping(value: JsonValue, context: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value
