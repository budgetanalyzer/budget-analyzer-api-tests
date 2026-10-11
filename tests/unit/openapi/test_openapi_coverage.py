from __future__ import annotations

import pytest

from api_tests.config import repository_root
from api_tests.coverage import (
    DeferredAdminOperation,
    MarkerCounts,
    build_marker_counts,
    build_operation_coverage,
    collect_openapi_markers_from_tests,
    load_deferred_admin_operations,
    missing_operation_ids,
    placeholder_operation_ids,
)
from api_tests.openapi import (
    HttpMethod,
    JsonValue,
    OpenApiOperation,
    iter_operations,
    load_snapshot,
)


def test_openapi_snapshot_operation_coverage() -> None:
    root = repository_root()
    openapi_doc = load_snapshot(root / "schemas" / "openapi.json")
    deferred = load_deferred_admin_operations(root / "schemas" / "deferred-admin-operations.yaml")
    coverage = build_operation_coverage(
        iter_operations(openapi_doc),
        deferred,
        collect_openapi_markers_from_tests(root / "tests"),
        source="snapshot",
    )

    missing = missing_operation_ids(coverage)
    deferred_ids = {row.operation_id for row in coverage if row.status == "deferred_admin"}

    assert not missing, "OpenAPI operations missing pytest.mark.openapi markers: " + ", ".join(
        missing
    )
    assert deferred_ids == {operation.operation_id for operation in deferred}
    assert not placeholder_operation_ids(coverage), (
        "OpenAPI operations still covered only by placeholder markers: "
        + ", ".join(placeholder_operation_ids(coverage))
    )


def test_iter_operations_requires_operation_id() -> None:
    openapi_doc: dict[str, JsonValue] = {
        "paths": {
            "/v1/example": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                        },
                    },
                },
            },
        },
    }

    with pytest.raises(ValueError, match="GET /v1/example is missing operationId"):
        list(iter_operations(openapi_doc))


def test_iter_operations_ignores_non_operation_path_fields() -> None:
    openapi_doc: dict[str, JsonValue] = {
        "paths": {
            "/v1/example": {
                "parameters": [],
                "get": {
                    "operationId": "getExample",
                    "responses": {},
                },
            },
        },
    }

    operations = tuple(iter_operations(openapi_doc))

    assert len(operations) == 1
    assert operations[0].operation_id == "getExample"
    assert operations[0].method == "GET"
    assert operations[0].path == "/v1/example"


def test_coverage_fails_new_non_deferred_operations_without_markers() -> None:
    openapi_doc: dict[str, JsonValue] = {
        "paths": {
            "/v1/new": {
                "get": {
                    "operationId": "newOperation",
                    "responses": {},
                },
            },
        },
    }

    coverage = build_operation_coverage(
        iter_operations(openapi_doc),
        (),
        {},
        source="snapshot",
    )

    assert missing_operation_ids(coverage) == ("newOperation",)
    assert coverage[0].status == "missing"


def test_deferred_admin_operations_report_separately() -> None:
    openapi_doc: dict[str, JsonValue] = {
        "paths": {
            "/v1/admin": {
                "post": {
                    "operationId": "adminOperation",
                    "responses": {},
                },
            },
        },
    }

    coverage = build_operation_coverage(
        iter_operations(openapi_doc),
        (),
        {
            "adminOperation": MarkerCounts(marker_count=1, placeholder_marker_count=1),
        },
        source="snapshot",
    )
    assert coverage[0].status == "placeholder"

    deferred_coverage = build_operation_coverage(
        iter_operations(openapi_doc),
        (
            DeferredAdminOperation(
                operation_id="adminOperation",
                method="POST",
                path="/v1/admin",
            ),
        ),
        {},
        source="snapshot",
    )
    assert deferred_coverage[0].status == "deferred_admin"
    assert deferred_coverage[0].deferred_admin


def test_duplicate_markers_and_placeholder_counts_are_visible() -> None:
    counts = build_marker_counts(
        (
            ("listViews", True),
            ("listViews", True),
            ("createView", True),
            ("createView", False),
        )
    )

    assert counts["listViews"].marker_count == 2
    assert counts["listViews"].placeholder_marker_count == 2
    assert counts["createView"].marker_count == 2
    assert counts["createView"].placeholder_marker_count == 1


def test_coverage_rejects_unrecognized_marker_operation_ids() -> None:
    operation = _operation("knownOperation", "GET", "/v1/known")

    with pytest.raises(
        ValueError,
        match=r"unrecognized pytest\.mark\.openapi operation ids: removedOperation",
    ):
        build_operation_coverage(
            (operation,),
            (),
            {"removedOperation": MarkerCounts(marker_count=1)},
            source="snapshot",
        )


def test_coverage_rejects_stale_deferred_method_or_path() -> None:
    operation = _operation("adminOperation", "POST", "/v1/admin")

    with pytest.raises(
        ValueError,
        match=(
            "stale deferred admin method/path for adminOperation: manifest has "
            "PUT /v1/admin, OpenAPI has POST /v1/admin"
        ),
    ):
        build_operation_coverage(
            (operation,),
            (
                DeferredAdminOperation(
                    operation_id="adminOperation",
                    method="PUT",
                    path="/v1/admin",
                ),
            ),
            {},
            source="snapshot",
        )


def _operation(operation_id: str, method: HttpMethod, path: str) -> OpenApiOperation:
    document: dict[str, JsonValue] = {
        "paths": {
            path: {
                method.lower(): {
                    "operationId": operation_id,
                    "responses": {},
                },
            },
        },
    }
    return next(iter(iter_operations(document)))
