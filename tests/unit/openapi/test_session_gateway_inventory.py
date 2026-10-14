from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_tests.config import repository_root
from api_tests.openapi import JsonValue, load_snapshot
from api_tests.session_gateway_inventory import (
    SessionGatewayCase,
    SessionGatewayRoute,
    load_session_gateway_inventory,
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
    coverage = validate_session_gateway_inventory(inventory, document)

    assert {row.status for row in coverage} == {"covered", "deferred", "untestable"}
    assert all(row.reason and row.prerequisite for row in coverage if row.status == "deferred")
    assert all(row.reason and row.boundary for row in coverage if row.status == "untestable")
    assert {row.openapi_operation_id for row in coverage if row.openapi_operation_id} == {
        "logout",
        "getCurrentUser",
        "getSessionStatus",
    }


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
    coverage = validate_session_gateway_inventory((route,), document)
    artifact_path = tmp_path / "coverage.json"

    write_session_gateway_coverage_artifact(artifact_path, coverage)

    artifact = artifact_path.read_text(encoding="utf-8")
    assert artifact.endswith("\n")
    assert '"contract": "session_gateway"' in artifact
    assert '"test_marker_count": 0' in artifact
    assert "cookie" not in artifact.lower()
    assert "authorization" not in artifact.lower()


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
