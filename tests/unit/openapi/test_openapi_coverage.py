from __future__ import annotations

from pathlib import Path

import pytest

from api_tests.config import repository_root
from api_tests.coverage import (
    DeferredAdminOperation,
    MarkerCounts,
    OperationKey,
    build_marker_counts,
    build_operation_coverage,
    collect_openapi_markers_from_tests,
    collect_session_gateway_case_markers_from_tests,
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
    markers = collect_openapi_markers_from_tests(root / "tests")
    coverage = build_operation_coverage(
        iter_operations(openapi_doc),
        deferred,
        {key: counts for key, counts in markers.items() if key.contract == "unified"},
        source="snapshot",
    )

    missing = missing_operation_ids(coverage)
    deferred_ids = {row.operation_id for row in coverage if row.deferred_admin}

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
            OperationKey("unified", "adminOperation"): MarkerCounts(
                marker_count=1, placeholder_marker_count=1
            ),
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
    assert deferred_coverage[0].status == "deferred"
    assert deferred_coverage[0].deferred_admin


def test_duplicate_markers_and_placeholder_counts_are_visible() -> None:
    counts = build_marker_counts(
        (
            ("unified", "listViews", True),
            ("unified", "listViews", True),
            ("unified", "createView", True),
            ("unified", "createView", False),
            ("session_gateway", "listViews", False),
        )
    )

    assert counts[OperationKey("unified", "listViews")].marker_count == 2
    assert counts[OperationKey("unified", "listViews")].placeholder_marker_count == 2
    assert counts[OperationKey("unified", "createView")].marker_count == 2
    assert counts[OperationKey("unified", "createView")].placeholder_marker_count == 1
    assert counts[OperationKey("session_gateway", "listViews")].marker_count == 1


def test_coverage_rejects_unrecognized_marker_operation_ids() -> None:
    operation = _operation("knownOperation", "GET", "/v1/known")

    with pytest.raises(
        ValueError,
        match=r"unrecognized pytest\.mark\.openapi operations: unified:removedOperation",
    ):
        build_operation_coverage(
            (operation,),
            (),
            {OperationKey("unified", "removedOperation"): MarkerCounts(marker_count=1)},
            source="snapshot",
        )


def test_same_operation_id_in_two_contracts_does_not_merge_coverage() -> None:
    unified = _operation("sharedId", "GET", "/v1/unified")
    session_document: dict[str, JsonValue] = {
        "paths": {
            "/auth/v1/shared": {
                "get": {"operationId": "sharedId", "responses": {}},
            }
        }
    }
    session_gateway = next(iter(iter_operations(session_document, contract="session_gateway")))

    coverage = build_operation_coverage(
        (unified, session_gateway),
        (),
        {OperationKey("session_gateway", "sharedId"): MarkerCounts(marker_count=1)},
        source="snapshot",
    )

    assert [(row.contract, row.status) for row in coverage] == [
        ("session_gateway", "covered"),
        ("unified", "missing"),
    ]


def test_static_marker_collection_supports_contract_qualifier(tmp_path: Path) -> None:
    test_path = tmp_path / "test_contract.py"
    test_path.write_text(
        """
import pytest

@pytest.mark.openapi("shared")
@pytest.mark.openapi("shared", contract="session_gateway")
def test_shared_operation_ids():
    pass
""".lstrip(),
        encoding="utf-8",
    )

    counts = collect_openapi_markers_from_tests(tmp_path)

    assert counts[OperationKey("unified", "shared")].marker_count == 1
    assert counts[OperationKey("session_gateway", "shared")].marker_count == 1


def test_static_session_gateway_case_marker_collection_counts_exact_ids(tmp_path: Path) -> None:
    test_path = tmp_path / "test_session_gateway.py"
    test_path.write_text(
        """
import pytest

@pytest.mark.session_gateway_case("current-user-authenticated")
@pytest.mark.session_gateway_case("heartbeat-active-session")
def test_heartbeat():
    pass

@pytest.mark.session_gateway_case("current-user-authenticated")
def test_user():
    pass
""".lstrip(),
        encoding="utf-8",
    )

    assert collect_session_gateway_case_markers_from_tests(tmp_path) == {
        "current-user-authenticated": 2,
        "heartbeat-active-session": 1,
    }


def test_static_session_gateway_case_marker_requires_literal(tmp_path: Path) -> None:
    test_path = tmp_path / "test_invalid_case.py"
    test_path.write_text(
        """
import pytest

CASE_ID = "current-user-authenticated"

@pytest.mark.session_gateway_case(CASE_ID)
def test_user():
    pass
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="case id must be a string literal"):
        collect_session_gateway_case_markers_from_tests(tmp_path)


@pytest.mark.parametrize(
    ("marker", "message"),
    [
        (
            '@pytest.mark.openapi("operation", namespace="session_gateway")',
            "accepts only the contract keyword",
        ),
        (
            '@pytest.mark.openapi("operation", contract="unknown")',
            "contract must be 'unified' or 'session_gateway'",
        ),
        (
            '@pytest.mark.openapi("operation", contract=CONTRACT)',
            "contract must be a string literal",
        ),
    ],
)
def test_static_marker_collection_rejects_invalid_contract_grammar(
    tmp_path: Path,
    marker: str,
    message: str,
) -> None:
    test_path = tmp_path / "test_invalid_marker.py"
    test_path.write_text(
        f"import pytest\nCONTRACT = 'session_gateway'\n{marker}\ndef test_case(): pass\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        collect_openapi_markers_from_tests(tmp_path)


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
