from __future__ import annotations

import ast
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

import yaml

from api_tests.openapi import (
    ForbiddenResponseDeferral,
    ForbiddenResponseDeferralReason,
    HttpMethod,
    OpenApiContractKey,
    OpenApiOperation,
)

CoverageSource = Literal["live", "snapshot"]
CoverageStatus = Literal["covered", "placeholder", "missing", "deferred"]
ForbiddenResponseCoverageStatus = Literal["covered", "deferred"]


@dataclass(frozen=True, slots=True)
class DeferredAdminOperation:
    operation_id: str
    method: HttpMethod
    path: str


@dataclass(frozen=True, slots=True)
class MarkerCounts:
    marker_count: int = 0
    placeholder_marker_count: int = 0


@dataclass(frozen=True, slots=True, order=True)
class OperationKey:
    contract: OpenApiContractKey
    operation_id: str


@dataclass(frozen=True, slots=True)
class OperationCoverage:
    contract: OpenApiContractKey
    operation_id: str
    method: HttpMethod
    path: str
    source: CoverageSource
    deferred_admin: bool
    marker_count: int
    placeholder_marker_count: int
    status: CoverageStatus


@dataclass(frozen=True, slots=True)
class ForbiddenResponseCoverage:
    operation_id: str
    method: HttpMethod
    path: str
    status: ForbiddenResponseCoverageStatus
    marker_count: int
    placeholder_marker_count: int
    deferral_reason: ForbiddenResponseDeferralReason | None


def load_deferred_admin_operations(path: Path) -> tuple[DeferredAdminOperation, ...]:
    with path.open("r", encoding="utf-8") as deferred_file:
        raw_manifest = yaml.safe_load(deferred_file)

    if not isinstance(raw_manifest, dict):
        raise ValueError(f"deferred admin manifest must contain a YAML mapping: {path}")

    raw_operations = raw_manifest.get("deferred_admin_operations")
    if not isinstance(raw_operations, list):
        raise ValueError(
            f"deferred admin manifest must contain list field 'deferred_admin_operations': {path}"
        )

    operations: list[DeferredAdminOperation] = []
    for index, raw_operation in enumerate(raw_operations):
        if not isinstance(raw_operation, dict):
            raise ValueError(f"deferred admin operation #{index + 1} must be a mapping: {path}")
        operation_id = raw_operation.get("operationId")
        method = raw_operation.get("method")
        operation_path = raw_operation.get("path")
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError(f"deferred admin operation #{index + 1} is missing operationId")
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(
                f"deferred admin operation {operation_id} has unsupported method: {method}"
            )
        if not isinstance(operation_path, str) or not operation_path.startswith("/"):
            raise ValueError(f"deferred admin operation {operation_id} has invalid path")
        operations.append(
            DeferredAdminOperation(
                operation_id=operation_id,
                method=method,
                path=operation_path,
            )
        )
    return tuple(operations)


def build_marker_counts(
    markers: Iterable[tuple[OpenApiContractKey, str, bool]],
) -> Mapping[OperationKey, MarkerCounts]:
    marker_counts: Counter[OperationKey] = Counter()
    placeholder_counts: Counter[OperationKey] = Counter()

    for contract, operation_id, is_placeholder in markers:
        key = OperationKey(contract=contract, operation_id=operation_id)
        marker_counts[key] += 1
        if is_placeholder:
            placeholder_counts[key] += 1

    operation_keys = marker_counts.keys() | placeholder_counts.keys()
    return {
        operation_key: MarkerCounts(
            marker_count=marker_counts[operation_key],
            placeholder_marker_count=placeholder_counts[operation_key],
        )
        for operation_key in sorted(operation_keys)
    }


def build_operation_coverage(
    operations: Iterable[OpenApiOperation],
    deferred_admin_operations: Iterable[DeferredAdminOperation],
    marker_counts: Mapping[OperationKey, MarkerCounts],
    *,
    source: CoverageSource,
) -> tuple[OperationCoverage, ...]:
    operation_list = tuple(operations)
    deferred_list = tuple(deferred_admin_operations)
    _validate_inventory(operation_list, deferred_list, marker_counts)
    deferred_by_id = {deferred.operation_id: deferred for deferred in deferred_list}
    coverage: list[OperationCoverage] = []
    for operation in sorted(
        operation_list, key=lambda item: (item.path, item.method, item.operation_id)
    ):
        operation_key = OperationKey(operation.contract, operation.operation_id)
        counts = marker_counts.get(operation_key, MarkerCounts())
        deferred_admin = (
            operation.contract == "unified" and operation.operation_id in deferred_by_id
        )
        coverage.append(
            OperationCoverage(
                contract=operation.contract,
                operation_id=operation.operation_id,
                method=operation.method,
                path=operation.path,
                source=source,
                deferred_admin=deferred_admin,
                marker_count=counts.marker_count,
                placeholder_marker_count=counts.placeholder_marker_count,
                status=_coverage_status(deferred_admin, counts),
            )
        )
    return tuple(coverage)


def _validate_inventory(
    operations: Sequence[OpenApiOperation],
    deferred_admin_operations: Sequence[DeferredAdminOperation],
    marker_counts: Mapping[OperationKey, MarkerCounts],
) -> None:
    operations_by_key: dict[OperationKey, OpenApiOperation] = {}
    for operation in operations:
        key = OperationKey(operation.contract, operation.operation_id)
        previous = operations_by_key.get(key)
        if previous is not None:
            raise ValueError(
                f"duplicate {operation.contract} OpenAPI operationId "
                f"{operation.operation_id!r}: {previous.method} {previous.path} and "
                f"{operation.method} {operation.path}"
            )
        operations_by_key[key] = operation

    unrecognized_markers = sorted(set(marker_counts) - operations_by_key.keys())
    if unrecognized_markers:
        raise ValueError(
            "unrecognized pytest.mark.openapi operations: "
            + ", ".join(
                f"{marker.contract}:{marker.operation_id}" for marker in unrecognized_markers
            )
        )

    seen_deferred_ids: set[str] = set()
    for deferred in deferred_admin_operations:
        if deferred.operation_id in seen_deferred_ids:
            raise ValueError(f"duplicate deferred admin operationId: {deferred.operation_id}")
        seen_deferred_ids.add(deferred.operation_id)
        matching_operation = operations_by_key.get(
            OperationKey(contract="unified", operation_id=deferred.operation_id)
        )
        if matching_operation is None:
            raise ValueError(
                "stale deferred admin operation is absent from OpenAPI: "
                f"{deferred.operation_id} ({deferred.method} {deferred.path})"
            )
        if matching_operation.method != deferred.method or matching_operation.path != deferred.path:
            raise ValueError(
                f"stale deferred admin method/path for {deferred.operation_id}: manifest has "
                f"{deferred.method} {deferred.path}, OpenAPI has "
                f"{matching_operation.method} {matching_operation.path}"
            )


def missing_operation_ids(coverage: Iterable[OperationCoverage]) -> tuple[str, ...]:
    return tuple(row.operation_id for row in coverage if row.status == "missing")


def placeholder_operation_ids(coverage: Iterable[OperationCoverage]) -> tuple[str, ...]:
    return tuple(row.operation_id for row in coverage if row.status == "placeholder")


def coverage_to_jsonable(coverage: Iterable[OperationCoverage]) -> list[dict[str, object]]:
    return [asdict(row) for row in coverage]


def write_coverage_artifact(path: Path, coverage: Iterable[OperationCoverage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(coverage_to_jsonable(coverage), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_forbidden_response_coverage(
    operations: Iterable[OpenApiOperation],
    deferrals: Iterable[ForbiddenResponseDeferral],
    marker_counts: Mapping[str, MarkerCounts],
) -> tuple[ForbiddenResponseCoverage, ...]:
    operation_list = tuple(operations)
    deferral_list = tuple(deferrals)
    operations_by_id = {operation.operation_id: operation for operation in operation_list}
    if len(operations_by_id) != len(operation_list):
        raise ValueError("documented 403 operations contain duplicate operation ids")

    unrecognized_markers = sorted(set(marker_counts) - operations_by_id.keys())
    if unrecognized_markers:
        raise ValueError(
            "unrecognized pytest.mark.forbidden_response operations: "
            + ", ".join(unrecognized_markers)
        )

    placeholder_only = sorted(
        operation_id
        for operation_id, counts in marker_counts.items()
        if counts.marker_count > 0 and counts.marker_count == counts.placeholder_marker_count
    )
    if placeholder_only:
        raise ValueError(
            "forbidden-response evidence has only placeholder tests: " + ", ".join(placeholder_only)
        )

    deferrals_by_id: dict[str, ForbiddenResponseDeferral] = {}
    for deferral in deferral_list:
        if deferral.operation_id in deferrals_by_id:
            raise ValueError(
                f"duplicate forbidden-response deferral operationId: {deferral.operation_id}"
            )
        operation = operations_by_id.get(deferral.operation_id)
        if operation is None:
            raise ValueError(
                "stale forbidden-response deferral is absent from documented OpenAPI 403 "
                f"responses: {deferral.operation_id} ({deferral.method} {deferral.path})"
            )
        if operation.method != deferral.method or operation.path != deferral.path:
            raise ValueError(
                f"stale forbidden-response method/path for {deferral.operation_id}: manifest has "
                f"{deferral.method} {deferral.path}, OpenAPI has "
                f"{operation.method} {operation.path}"
            )
        deferrals_by_id[deferral.operation_id] = deferral

    executable_ids = {
        operation_id
        for operation_id, counts in marker_counts.items()
        if counts.marker_count > counts.placeholder_marker_count
    }
    deferred_ids = set(deferrals_by_id)
    overlap = sorted(executable_ids & deferred_ids)
    if overlap:
        raise ValueError(
            "documented 403 responses have both executable evidence and deferrals: "
            + ", ".join(overlap)
        )
    missing = sorted(operations_by_id.keys() - executable_ids - deferred_ids)
    if missing:
        raise ValueError(
            "documented 403 responses have neither executable evidence nor deferrals: "
            + ", ".join(missing)
        )

    rows: list[ForbiddenResponseCoverage] = []
    for operation in sorted(
        operation_list, key=lambda item: (item.path, item.method, item.operation_id)
    ):
        counts = marker_counts.get(operation.operation_id, MarkerCounts())
        row_deferral = deferrals_by_id.get(operation.operation_id)
        rows.append(
            ForbiddenResponseCoverage(
                operation_id=operation.operation_id,
                method=operation.method,
                path=operation.path,
                status="deferred" if row_deferral is not None else "covered",
                marker_count=counts.marker_count,
                placeholder_marker_count=counts.placeholder_marker_count,
                deferral_reason=row_deferral.reason if row_deferral is not None else None,
            )
        )
    return tuple(rows)


def write_forbidden_response_coverage_artifact(
    path: Path,
    coverage: Iterable[ForbiddenResponseCoverage],
) -> None:
    rows = tuple(coverage)
    covered_ids = sorted(row.operation_id for row in rows if row.status == "covered")
    deferred_ids = sorted(row.operation_id for row in rows if row.status == "deferred")
    report: dict[str, object] = {
        "counts": {
            "covered": len(covered_ids),
            "deferred": len(deferred_ids),
            "total": len(rows),
        },
        "ids": {
            "covered": covered_ids,
            "deferred": deferred_ids,
        },
        "rows": [asdict(row) for row in rows],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def collect_openapi_markers_from_tests(
    tests_dir: Path,
) -> Mapping[OperationKey, MarkerCounts]:
    marker_entries: list[tuple[OpenApiContractKey, str, bool]] = []
    for test_path in sorted(tests_dir.rglob("test_*.py")):
        marker_entries.extend(_collect_openapi_markers_from_file(test_path))
    return build_marker_counts(marker_entries)


def collect_forbidden_response_markers_from_tests(
    tests_dir: Path,
) -> Mapping[str, MarkerCounts]:
    marker_counts: Counter[str] = Counter()
    placeholder_counts: Counter[str] = Counter()
    for test_path in sorted(tests_dir.rglob("test_*.py")):
        module = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
        for node in ast.walk(module):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            operation_ids = [
                operation_id
                for decorator in node.decorator_list
                if (operation_id := _forbidden_response_marker_operation(decorator)) is not None
            ]
            is_placeholder = any(
                _is_pytest_marker(decorator, "placeholder") for decorator in node.decorator_list
            )
            for operation_id in operation_ids:
                marker_counts[operation_id] += 1
                if is_placeholder:
                    placeholder_counts[operation_id] += 1

    all_operation_ids = marker_counts.keys() | placeholder_counts.keys()
    return {
        operation_id: MarkerCounts(
            marker_count=marker_counts[operation_id],
            placeholder_marker_count=placeholder_counts[operation_id],
        )
        for operation_id in sorted(all_operation_ids)
    }


def collect_session_gateway_case_markers_from_tests(tests_dir: Path) -> Mapping[str, int]:
    marker_counts: Counter[str] = Counter()
    for test_path in sorted(tests_dir.rglob("test_*.py")):
        module = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
        for node in ast.walk(module):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                case_id = _session_gateway_case_marker(decorator)
                if case_id is not None:
                    marker_counts[case_id] += 1
    return dict(sorted(marker_counts.items()))


def _coverage_status(deferred_admin: bool, counts: MarkerCounts) -> CoverageStatus:
    if deferred_admin:
        return "deferred"
    if counts.marker_count == 0:
        return "missing"
    if counts.placeholder_marker_count == counts.marker_count:
        return "placeholder"
    return "covered"


def _collect_openapi_markers_from_file(
    path: Path,
) -> Sequence[tuple[OpenApiContractKey, str, bool]]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    markers: list[tuple[OpenApiContractKey, str, bool]] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        operations = [
            operation
            for decorator in node.decorator_list
            if (operation := _openapi_marker_operation(decorator)) is not None
        ]
        if not operations:
            continue
        is_placeholder = any(
            _is_pytest_marker(decorator, "placeholder") for decorator in node.decorator_list
        )
        markers.extend(
            (operation.contract, operation.operation_id, is_placeholder) for operation in operations
        )
    return markers


def _openapi_marker_operation(node: ast.expr) -> OperationKey | None:
    if not isinstance(node, ast.Call):
        return None
    if not _is_pytest_marker(node.func, "openapi"):
        return None
    if len(node.args) != 1:
        raise ValueError("@pytest.mark.openapi requires exactly one positional operation id")
    operation_id = node.args[0]
    if not isinstance(operation_id, ast.Constant) or not isinstance(operation_id.value, str):
        raise ValueError("@pytest.mark.openapi operation id must be a string literal")
    contract: OpenApiContractKey = "unified"
    if node.keywords:
        if len(node.keywords) != 1 or node.keywords[0].arg != "contract":
            raise ValueError("@pytest.mark.openapi accepts only the contract keyword")
        raw_contract = node.keywords[0].value
        if not isinstance(raw_contract, ast.Constant) or not isinstance(raw_contract.value, str):
            raise ValueError("@pytest.mark.openapi contract must be a string literal")
        if raw_contract.value not in {"unified", "session_gateway"}:
            raise ValueError("@pytest.mark.openapi contract must be 'unified' or 'session_gateway'")
        contract = cast(OpenApiContractKey, raw_contract.value)
    return OperationKey(contract=contract, operation_id=operation_id.value)


def _session_gateway_case_marker(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not _is_pytest_marker(node.func, "session_gateway_case"):
        return None
    if len(node.args) != 1 or node.keywords:
        raise ValueError(
            "@pytest.mark.session_gateway_case requires exactly one positional case id"
        )
    case_id = node.args[0]
    if not isinstance(case_id, ast.Constant) or not isinstance(case_id.value, str):
        raise ValueError("@pytest.mark.session_gateway_case case id must be a string literal")
    if not case_id.value:
        raise ValueError("@pytest.mark.session_gateway_case case id must not be empty")
    return case_id.value


def _forbidden_response_marker_operation(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not _is_pytest_marker(node.func, "forbidden_response"):
        return None
    if len(node.args) != 1 or node.keywords:
        raise ValueError(
            "@pytest.mark.forbidden_response requires exactly one positional operation id"
        )
    operation_id = node.args[0]
    if not isinstance(operation_id, ast.Constant) or not isinstance(operation_id.value, str):
        raise ValueError("@pytest.mark.forbidden_response operation id must be a string literal")
    if not operation_id.value:
        raise ValueError("@pytest.mark.forbidden_response operation id must not be empty")
    return operation_id.value


def _is_pytest_marker(node: ast.expr, marker_name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == marker_name
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )
