from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from api_tests.openapi import HTTP_METHODS, HttpMethod, JsonValue, OpenApiDocument

DriftChange = Literal["added", "removed", "changed"]


@dataclass(frozen=True, slots=True)
class OpenApiDifference:
    location: str
    change: DriftChange
    baseline: str | None = None
    live: str | None = None


@dataclass(frozen=True, slots=True)
class _OperationContract:
    method: HttpMethod
    path: str
    parameters: JsonValue
    request_body: JsonValue
    responses: JsonValue


def compare_openapi_documents(
    baseline: OpenApiDocument,
    live: OpenApiDocument,
) -> tuple[OpenApiDifference, ...]:
    """Compare the public structural contract while ignoring document metadata and key order."""
    baseline_operations = _operation_contracts(baseline)
    live_operations = _operation_contracts(live)
    differences: list[OpenApiDifference] = []

    for operation_id in sorted(baseline_operations.keys() - live_operations.keys()):
        operation = baseline_operations[operation_id]
        differences.append(
            OpenApiDifference(
                location=f"operations.{operation_id}",
                change="removed",
                baseline=f"{operation.method} {operation.path}",
            )
        )
    for operation_id in sorted(live_operations.keys() - baseline_operations.keys()):
        operation = live_operations[operation_id]
        differences.append(
            OpenApiDifference(
                location=f"operations.{operation_id}",
                change="added",
                live=f"{operation.method} {operation.path}",
            )
        )
    for operation_id in sorted(baseline_operations.keys() & live_operations.keys()):
        baseline_operation = baseline_operations[operation_id]
        live_operation = live_operations[operation_id]
        _append_scalar_change(
            differences,
            f"operations.{operation_id}.method",
            baseline_operation.method,
            live_operation.method,
        )
        _append_scalar_change(
            differences,
            f"operations.{operation_id}.path",
            baseline_operation.path,
            live_operation.path,
        )
        _append_structural_change(
            differences,
            f"operations.{operation_id}.parameters",
            baseline_operation.parameters,
            live_operation.parameters,
        )
        _append_structural_change(
            differences,
            f"operations.{operation_id}.requestBody",
            baseline_operation.request_body,
            live_operation.request_body,
        )
        _append_structural_change(
            differences,
            f"operations.{operation_id}.responses",
            baseline_operation.responses,
            live_operation.responses,
        )

    _append_schema_differences(
        differences,
        _component_schemas(baseline),
        _component_schemas(live),
    )
    return tuple(differences)


def write_drift_artifact(
    path: Path,
    differences: tuple[OpenApiDifference, ...],
    *,
    baseline_path: str,
    live_url: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "baseline": baseline_path,
        "live_url": live_url,
        "status": "match" if not differences else "different",
        "difference_count": len(differences),
        "differences": [asdict(difference) for difference in differences],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _operation_contracts(document: OpenApiDocument) -> dict[str, _OperationContract]:
    raw_paths = document.get("paths")
    if not isinstance(raw_paths, dict):
        raise ValueError("OpenAPI document must contain object field 'paths'")

    contracts: dict[str, _OperationContract] = {}
    for path, raw_path_item in raw_paths.items():
        if not isinstance(raw_path_item, dict):
            continue
        path_parameters = raw_path_item.get("parameters", [])
        for method, raw_operation in raw_path_item.items():
            if method not in HTTP_METHODS:
                continue
            if not isinstance(raw_operation, dict):
                raise ValueError(f"OpenAPI operation {method.upper()} {path} must be an object")
            operation_id = raw_operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                raise ValueError(
                    f"OpenAPI operation {method.upper()} {path} is missing operationId"
                )
            if operation_id in contracts:
                raise ValueError(f"duplicate OpenAPI operationId: {operation_id}")
            contracts[operation_id] = _OperationContract(
                method=_http_method(method),
                path=path,
                parameters=[path_parameters, raw_operation.get("parameters", [])],
                request_body=raw_operation.get("requestBody"),
                responses=raw_operation.get("responses"),
            )
    return contracts


def _component_schemas(document: OpenApiDocument) -> Mapping[str, JsonValue]:
    components = document.get("components")
    if components is None:
        return {}
    if not isinstance(components, dict):
        raise ValueError("OpenAPI field 'components' must be an object")
    schemas = components.get("schemas")
    if schemas is None:
        return {}
    if not isinstance(schemas, dict):
        raise ValueError("OpenAPI field 'components.schemas' must be an object")
    return schemas


def _append_schema_differences(
    differences: list[OpenApiDifference],
    baseline: Mapping[str, JsonValue],
    live: Mapping[str, JsonValue],
) -> None:
    for schema_name in sorted(baseline.keys() - live.keys()):
        differences.append(OpenApiDifference(f"components.schemas.{schema_name}", "removed"))
    for schema_name in sorted(live.keys() - baseline.keys()):
        differences.append(OpenApiDifference(f"components.schemas.{schema_name}", "added"))
    for schema_name in sorted(baseline.keys() & live.keys()):
        _append_structural_change(
            differences,
            f"components.schemas.{schema_name}",
            baseline[schema_name],
            live[schema_name],
        )


def _append_scalar_change(
    differences: list[OpenApiDifference],
    location: str,
    baseline: str,
    live: str,
) -> None:
    if baseline != live:
        differences.append(OpenApiDifference(location, "changed", baseline, live))


def _append_structural_change(
    differences: list[OpenApiDifference],
    location: str,
    baseline: JsonValue,
    live: JsonValue,
) -> None:
    if baseline != live:
        differences.append(OpenApiDifference(location, "changed"))


def _http_method(method: str) -> HttpMethod:
    normalized = method.upper()
    if normalized == "GET":
        return "GET"
    if normalized == "POST":
        return "POST"
    if normalized == "PUT":
        return "PUT"
    if normalized == "PATCH":
        return "PATCH"
    if normalized == "DELETE":
        return "DELETE"
    raise ValueError(f"unsupported OpenAPI method: {method}")
