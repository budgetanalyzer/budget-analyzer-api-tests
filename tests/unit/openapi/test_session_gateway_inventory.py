from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from api_tests.config import repository_root
from api_tests.coverage import (
    OperationCoverage,
    build_operation_coverage,
    collect_openapi_markers_from_tests,
    collect_session_gateway_case_markers_from_tests,
)
from api_tests.openapi import JsonValue, iter_operations, load_snapshot
from api_tests.session_gateway_inventory import (
    SessionGatewayCase,
    SessionGatewayRoute,
    build_session_gateway_coverage_report,
    load_session_gateway_inventory,
    session_gateway_coverage_lines,
    validate_session_gateway_contract,
    validate_session_gateway_inventory,
    write_session_gateway_coverage_artifact,
)


@pytest.mark.openapi("logout", contract="session_gateway")
def test_session_gateway_snapshot_and_inventory_are_complete() -> None:
    root = repository_root()
    document = load_snapshot(root / "schemas" / "session-gateway-openapi.yaml")
    inventory = load_session_gateway_inventory(
        root / "schemas" / "session-gateway-inbound-inventory.yaml"
    )

    validate_session_gateway_contract(document)
    marker_counts = collect_session_gateway_case_markers_from_tests(root / "tests")
    coverage = validate_session_gateway_inventory(inventory, document, marker_counts)

    assert {row.status for row in coverage} == {"covered", "deferred", "untestable"}
    assert Counter(row.status for row in coverage) == {
        "covered": 14,
        "deferred": 11,
        "untestable": 9,
    }
    assert len({(row.method, row.path) for row in coverage}) == 14
    assert all(row.test_marker_count > 0 for row in coverage if row.status == "covered")
    assert all(row.reason and row.prerequisite for row in coverage if row.status == "deferred")
    assert all(row.reason and row.boundary for row in coverage if row.status == "untestable")
    assert {row.openapi_operation_id for row in coverage if row.openapi_operation_id} == {
        "logout",
        "getCurrentUser",
        "getSessionStatus",
    }
    internal_revocation = next(
        row for row in coverage if row.case_id == "internal-user-session-revocation"
    )
    assert internal_revocation.status == "untestable"
    assert internal_revocation.exposure == "mesh_internal"
    assert internal_revocation.test_marker_count == 0
    assert internal_revocation.optional_indirect_proof is not None
    assert "does not isolate" in internal_revocation.optional_indirect_proof.limitation
    assert "admin identity" in internal_revocation.optional_indirect_proof.prerequisite


def test_snapshot_loader_accepts_yaml_and_json(tmp_path: Path) -> None:
    raw_document = {"openapi": "3.1.0", "paths": {}}
    json_path = tmp_path / "contract.json"
    yaml_path = tmp_path / "contract.yaml"
    json_path.write_text(json.dumps(raw_document), encoding="utf-8")
    yaml_path.write_text("openapi: 3.1.0\npaths: {}\n", encoding="utf-8")

    assert load_snapshot(json_path) == raw_document
    assert load_snapshot(yaml_path) == raw_document


def test_logout_contract_rejects_stale_200_response() -> None:
    document = _session_gateway_document(logout_status="200")

    with pytest.raises(ValueError, match="logout must document response 302"):
        validate_session_gateway_contract(document)


def test_inventory_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(
        """
routes:
  - method: GET
    path: /logout
    owner: controller
    exposure: public
    cases:
      - id: duplicate
        behavior: first
        status: covered
      - id: duplicate
        behavior: second
        status: covered
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate Session Gateway case id: duplicate"):
        load_session_gateway_inventory(inventory_path)


@pytest.mark.parametrize("missing_field", ["reason", "prerequisite"])
def test_inventory_rejects_incomplete_deferral(
    tmp_path: Path,
    missing_field: str,
) -> None:
    lines = [
        "routes:",
        "  - method: GET",
        "    path: /logout",
        "    owner: controller",
        "    exposure: public",
        "    cases:",
        "      - id: deferred-case",
        "        behavior: dependency failure",
        "        status: deferred",
    ]
    if missing_field != "reason":
        lines.append("        reason: controlled fault unavailable")
    if missing_field != "prerequisite":
        lines.append("        prerequisite: deployment fault injection")
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="requires reason and prerequisite"):
        load_session_gateway_inventory(inventory_path)


def test_inventory_rejects_stale_openapi_route() -> None:
    route = SessionGatewayRoute(
        method="POST",
        path="/logout",
        owner="controller",
        exposure="public",
        openapi_operation_id="logout",
        cases=(SessionGatewayCase("logout", "redirect", "covered"),),
    )

    with pytest.raises(
        ValueError,
        match="stale Session Gateway inventory route for logout",
    ):
        validate_session_gateway_inventory((route,), _session_gateway_document())


def test_inventory_rejects_snapshot_operation_without_route() -> None:
    with pytest.raises(
        ValueError,
        match="Session Gateway snapshot operations missing from inventory: logout",
    ):
        validate_session_gateway_inventory((), _session_gateway_document())


def test_session_gateway_artifact_is_stable_and_classification_only(tmp_path: Path) -> None:
    document = _session_gateway_document()
    route = SessionGatewayRoute(
        method="GET",
        path="/logout",
        owner="controller",
        exposure="public",
        openapi_operation_id="logout",
        cases=(SessionGatewayCase("logout", "redirect", "covered"),),
    )
    coverage = validate_session_gateway_inventory((route,), document, {"logout": 1})
    report = build_session_gateway_coverage_report((_operation_coverage(),), coverage)
    artifact_path = tmp_path / "coverage.json"

    write_session_gateway_coverage_artifact(artifact_path, report)

    artifact = artifact_path.read_text(encoding="utf-8")
    assert artifact.endswith("\n")
    assert '"contract": "session_gateway"' in artifact
    assert '"full_session_gateway_behavior_coverage": true' in artifact
    assert '"operation_coverage"' in artifact
    assert '"scenario_coverage"' in artifact
    assert "cookie" not in artifact.lower()
    assert "authorization" not in artifact.lower()


def test_report_keeps_operation_and_scenario_coverage_separate() -> None:
    root = repository_root()
    document = load_snapshot(root / "schemas" / "session-gateway-openapi.yaml")
    openapi_markers = collect_openapi_markers_from_tests(root / "tests")
    operation_coverage = build_operation_coverage(
        iter_operations(document, contract="session_gateway"),
        (),
        {
            key: counts
            for key, counts in openapi_markers.items()
            if key.contract == "session_gateway"
        },
        source="snapshot",
    )
    inventory = load_session_gateway_inventory(
        root / "schemas" / "session-gateway-inbound-inventory.yaml"
    )
    scenario_coverage = validate_session_gateway_inventory(
        inventory,
        document,
        collect_session_gateway_case_markers_from_tests(root / "tests"),
    )

    report = build_session_gateway_coverage_report(operation_coverage, scenario_coverage)

    assert [(row.status, row.count) for row in report.operation_coverage.classifications] == [
        ("covered", 3),
        ("deferred", 0),
        ("missing", 0),
        ("placeholder", 0),
    ]
    assert [(row.status, row.count) for row in report.scenario_coverage.classifications] == [
        ("covered", 14),
        ("deferred", 11),
        ("untestable", 9),
    ]
    assert report.claims.all_known_inbound_routes_inventoried
    assert report.claims.all_currently_executable_public_cases_covered
    assert not report.claims.full_session_gateway_behavior_coverage
    assert report.claims.uncovered_executable_case_ids == ()
    assert report.scenario_coverage.route_count == 14

    lines = session_gateway_coverage_lines(report)
    assert (
        "session_gateway_operation_covered: 3; ids: getCurrentUser, getSessionStatus, logout"
        in lines
    )
    assert any(line.startswith("session_gateway_scenario_deferred: 11; ids: ") for line in lines)
    assert "full_session_gateway_behavior_coverage: false" in lines


def test_report_cannot_claim_complete_behavior_with_deferred_or_untestable_cases() -> None:
    document = _session_gateway_document()
    routes = (
        SessionGatewayRoute(
            method="GET",
            path="/logout",
            owner="controller",
            exposure="public",
            openapi_operation_id="logout",
            cases=(
                SessionGatewayCase("logout", "redirect", "covered"),
                SessionGatewayCase(
                    "logout-failure",
                    "fails",
                    "deferred",
                    reason="controlled_fault_required",
                    prerequisite="approved fault injection",
                ),
            ),
        ),
        SessionGatewayRoute(
            method="GET",
            path="/actuator/health",
            owner="actuator",
            exposure="deployment_operational",
            openapi_operation_id=None,
            cases=(
                SessionGatewayCase(
                    "health",
                    "health",
                    "untestable",
                    reason="not_public",
                    boundary="public ingress omits the route",
                ),
            ),
        ),
    )
    scenarios = validate_session_gateway_inventory(routes, document, {"logout": 1})

    report = build_session_gateway_coverage_report((_operation_coverage(),), scenarios)

    assert report.claims.all_currently_executable_public_cases_covered
    assert not report.claims.full_session_gateway_behavior_coverage


def test_report_identifies_executable_case_without_marker() -> None:
    document = _session_gateway_document()
    route = SessionGatewayRoute(
        method="GET",
        path="/logout",
        owner="controller",
        exposure="public",
        openapi_operation_id="logout",
        cases=(SessionGatewayCase("logout", "redirect", "covered"),),
    )
    scenarios = validate_session_gateway_inventory((route,), document)

    report = build_session_gateway_coverage_report((_operation_coverage(),), scenarios)

    assert not report.claims.all_currently_executable_public_cases_covered
    assert not report.claims.full_session_gateway_behavior_coverage
    assert report.claims.uncovered_executable_case_ids == ("logout",)


def test_inventory_reports_covered_case_marker_counts() -> None:
    document = _session_gateway_document()
    route = SessionGatewayRoute(
        method="GET",
        path="/logout",
        owner="controller",
        exposure="public",
        openapi_operation_id="logout",
        cases=(SessionGatewayCase("logout", "redirect", "covered"),),
    )

    coverage = validate_session_gateway_inventory((route,), document, {"logout": 2})

    assert coverage[0].test_marker_count == 2


def test_inventory_rejects_unknown_case_marker() -> None:
    with pytest.raises(ValueError, match=r"unrecognized pytest\.mark\.session_gateway_case ids"):
        validate_session_gateway_inventory((), {"paths": {}}, {"removed-case": 1})


def test_inventory_rejects_marker_for_deferred_case() -> None:
    route = SessionGatewayRoute(
        method="GET",
        path="/dependency",
        owner="controller",
        exposure="public",
        openapi_operation_id=None,
        cases=(
            SessionGatewayCase(
                "dependency-failure",
                "dependency fails",
                "deferred",
                reason="fault control unavailable",
                prerequisite="fault injection lane",
            ),
        ),
    )

    with pytest.raises(ValueError, match="markers may reference only covered cases"):
        validate_session_gateway_inventory(
            (route,),
            {"paths": {}},
            {"dependency-failure": 1},
        )


def test_inventory_rejects_internal_case_classified_as_covered() -> None:
    route = SessionGatewayRoute(
        method="DELETE",
        path="/internal/v1/sessions/users/{userId}",
        owner="controller",
        exposure="mesh_internal",
        openapi_operation_id=None,
        cases=(SessionGatewayCase("internal-revocation", "revokes sessions", "covered"),),
    )

    with pytest.raises(ValueError, match=r"covered.*must use public exposure"):
        validate_session_gateway_inventory((route,), {"paths": {}})


def test_inventory_rejects_untestable_case_with_planned_prerequisite(tmp_path: Path) -> None:
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(
        """
routes:
  - method: DELETE
    path: /internal
    owner: controller
    exposure: mesh_internal
    cases:
      - id: internal
        behavior: internal behavior
        status: untestable
        reason: mesh only
        prerequisite: forge identity
        boundary: public ingress only
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must not declare prerequisite"):
        load_session_gateway_inventory(inventory_path)


def test_inventory_rejects_indirect_proof_on_deferred_case(tmp_path: Path) -> None:
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(
        """
routes:
  - method: GET
    path: /dependency
    owner: controller
    exposure: public
    cases:
      - id: dependency
        behavior: dependency behavior
        status: deferred
        reason: controlled fault required
        prerequisite: approved fault injection
        optional_indirect_proof:
          scenario: indirect scenario
          prerequisite: indirect prerequisite
          limitation: indirect limitation
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"only untestable.*optional_indirect_proof"):
        load_session_gateway_inventory(inventory_path)


def _session_gateway_document(*, logout_status: str = "302") -> dict[str, JsonValue]:
    response: dict[str, JsonValue]
    if logout_status == "302":
        response = {
            "description": "Found",
            "headers": {
                "Location": {"schema": {"type": "string", "format": "uri"}},
            },
        }
    else:
        response = {"description": "OK"}
    return {
        "paths": {
            "/logout": {
                "get": {
                    "operationId": "logout",
                    "responses": {logout_status: response},
                }
            }
        }
    }


def _operation_coverage() -> OperationCoverage:
    return OperationCoverage(
        contract="session_gateway",
        operation_id="logout",
        method="GET",
        path="/logout",
        source="snapshot",
        deferred_admin=False,
        marker_count=1,
        placeholder_marker_count=0,
        status="covered",
    )
