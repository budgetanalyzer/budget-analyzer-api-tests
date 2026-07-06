from __future__ import annotations

import ast
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import yaml

from api_tests.openapi import HttpMethod, OpenApiOperation

CoverageSource = Literal["live", "snapshot"]
CoverageStatus = Literal["covered", "placeholder", "missing", "deferred_admin"]


@dataclass(frozen=True, slots=True)
class DeferredAdminOperation:
    operation_id: str
    method: HttpMethod
    path: str


@dataclass(frozen=True, slots=True)
class MarkerCounts:
    marker_count: int = 0
    placeholder_marker_count: int = 0


@dataclass(frozen=True, slots=True)
class OperationCoverage:
    operation_id: str
    method: HttpMethod
    path: str
    source: CoverageSource
    deferred_admin: bool
    marker_count: int
    placeholder_marker_count: int
    status: CoverageStatus


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


def build_marker_counts(markers: Iterable[tuple[str, bool]]) -> Mapping[str, MarkerCounts]:
    marker_counts: Counter[str] = Counter()
    placeholder_counts: Counter[str] = Counter()

    for operation_id, is_placeholder in markers:
        marker_counts[operation_id] += 1
        if is_placeholder:
            placeholder_counts[operation_id] += 1

    operation_ids = marker_counts.keys() | placeholder_counts.keys()
    return {
        operation_id: MarkerCounts(
            marker_count=marker_counts[operation_id],
            placeholder_marker_count=placeholder_counts[operation_id],
        )
        for operation_id in sorted(operation_ids)
    }


def build_operation_coverage(
    operations: Iterable[OpenApiOperation],
    deferred_admin_operations: Iterable[DeferredAdminOperation],
    marker_counts: Mapping[str, MarkerCounts],
    *,
    source: CoverageSource,
) -> tuple[OperationCoverage, ...]:
    deferred_by_id = {deferred.operation_id: deferred for deferred in deferred_admin_operations}
    coverage: list[OperationCoverage] = []
    for operation in sorted(
        operations, key=lambda item: (item.path, item.method, item.operation_id)
    ):
        counts = marker_counts.get(operation.operation_id, MarkerCounts())
        deferred_admin = operation.operation_id in deferred_by_id
        coverage.append(
            OperationCoverage(
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


def collect_openapi_markers_from_tests(tests_dir: Path) -> Mapping[str, MarkerCounts]:
    marker_entries: list[tuple[str, bool]] = []
    for test_path in sorted(tests_dir.rglob("test_*.py")):
        marker_entries.extend(_collect_openapi_markers_from_file(test_path))
    return build_marker_counts(marker_entries)


def _coverage_status(deferred_admin: bool, counts: MarkerCounts) -> CoverageStatus:
    if deferred_admin:
        return "deferred_admin"
    if counts.marker_count == 0:
        return "missing"
    if counts.placeholder_marker_count == counts.marker_count:
        return "placeholder"
    return "covered"


def _collect_openapi_markers_from_file(path: Path) -> Sequence[tuple[str, bool]]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    markers: list[tuple[str, bool]] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        operation_ids = [
            operation_id
            for decorator in node.decorator_list
            if (operation_id := _openapi_marker_operation_id(decorator)) is not None
        ]
        if not operation_ids:
            continue
        is_placeholder = any(
            _is_pytest_marker(decorator, "placeholder") for decorator in node.decorator_list
        )
        markers.extend((operation_id, is_placeholder) for operation_id in operation_ids)
    return markers


def _openapi_marker_operation_id(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not _is_pytest_marker(node.func, "openapi"):
        return None
    if len(node.args) != 1 or node.keywords:
        raise ValueError("@pytest.mark.openapi requires exactly one positional operation id")
    operation_id = node.args[0]
    if not isinstance(operation_id, ast.Constant) or not isinstance(operation_id.value, str):
        raise ValueError("@pytest.mark.openapi operation id must be a string literal")
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
