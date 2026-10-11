from __future__ import annotations

import json
from pathlib import Path

from api_tests.openapi import JsonValue, load_snapshot
from api_tests.openapi_drift import compare_openapi_documents, write_drift_artifact


def test_drift_detects_same_operation_request_and_response_changes_without_writing_snapshot(
    tmp_path: Path,
) -> None:
    snapshot_path = tmp_path / "openapi.json"
    baseline = _document()
    snapshot_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    original_bytes = snapshot_path.read_bytes()
    live = _document()
    operation = _operation(live)
    operation["requestBody"] = {"required": True}
    operation["responses"] = {"201": {"description": "Created"}}

    differences = compare_openapi_documents(load_snapshot(snapshot_path), live)

    assert [(item.location, item.change) for item in differences] == [
        ("operations.createExample.requestBody", "changed"),
        ("operations.createExample.responses", "changed"),
    ]
    assert snapshot_path.read_bytes() == original_bytes


def test_drift_reports_method_path_parameter_and_component_schema_changes() -> None:
    baseline = _document()
    live = _document()
    operation = _operation(live)
    operation["parameters"] = [{"in": "query", "name": "limit", "schema": {"type": "integer"}}]
    live["paths"] = {
        "/v2/examples": {
            "put": operation,
        }
    }
    components = live["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    schemas["Example"] = {"type": "object", "required": ["id"]}

    differences = compare_openapi_documents(baseline, live)

    assert [(item.location, item.change) for item in differences] == [
        ("operations.createExample.method", "changed"),
        ("operations.createExample.path", "changed"),
        ("operations.createExample.parameters", "changed"),
        ("components.schemas.Example", "changed"),
    ]


def test_drift_reports_added_and_removed_operations() -> None:
    baseline = _document()
    live = _document()
    operation = _operation(live)
    operation["operationId"] = "replacementOperation"

    differences = compare_openapi_documents(baseline, live)

    assert [(item.location, item.change) for item in differences] == [
        ("operations.createExample", "removed"),
        ("operations.replacementOperation", "added"),
    ]


def test_drift_ignores_server_metadata_and_mapping_key_order() -> None:
    baseline = _document()
    live: dict[str, JsonValue] = {
        "servers": [{"url": "https://different.invalid"}],
        "components": {"schemas": {"Example": {"properties": {}, "type": "object"}}},
        "paths": baseline["paths"],
        "openapi": "3.1.0",
    }

    assert compare_openapi_documents(baseline, live) == ()


def test_drift_artifact_contains_readable_locations(tmp_path: Path) -> None:
    output_path = tmp_path / "drift.json"
    differences = compare_openapi_documents(_document(), {"paths": {}})

    write_drift_artifact(
        output_path,
        differences,
        baseline_path="schemas/openapi.json",
        live_url="https://example.test/api-docs/openapi.json",
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "different"
    assert payload["differences"][0]["location"] == "operations.createExample"


def _document() -> dict[str, JsonValue]:
    return {
        "openapi": "3.1.0",
        "servers": [{"url": "https://baseline.invalid"}],
        "paths": {
            "/v1/examples": {
                "post": {
                    "operationId": "createExample",
                    "requestBody": {"required": False},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {"schemas": {"Example": {"type": "object", "properties": {}}}},
    }


def _operation(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    paths = document["paths"]
    assert isinstance(paths, dict)
    path_item = paths["/v1/examples"]
    assert isinstance(path_item, dict)
    operation = path_item["post"]
    assert isinstance(operation, dict)
    return operation
