from __future__ import annotations

import httpx
import pytest

from api_tests.openapi import JsonValue, OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi


def test_openapi_snapshot_fixture_loads_checked_in_document(
    openapi_snapshot: OpenApiDocument,
) -> None:
    assert isinstance(openapi_snapshot.get("paths"), dict)


def test_assert_response_matches_openapi_accepts_matching_object_response() -> None:
    openapi_doc = _openapi_doc(
        operation_id="getWidget",
        status=200,
        schema={"$ref": "#/components/schemas/Widget"},
        components={
            "Widget": {
                "type": "object",
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
        },
    )
    response = httpx.Response(200, json={"id": 1, "name": "primary"})

    assert_response_matches_openapi(response, "getWidget", 200, openapi_doc)


def test_assert_response_matches_openapi_accepts_matching_array_response() -> None:
    openapi_doc = _openapi_doc(
        operation_id="listWidgets",
        status=200,
        schema={
            "type": "array",
            "items": {"$ref": "#/components/schemas/Widget"},
        },
        components={
            "Widget": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
        },
    )
    response = httpx.Response(200, json=[{"name": "primary"}, {"name": "backup"}])

    assert_response_matches_openapi(response, "listWidgets", 200, openapi_doc)


def test_assert_response_matches_openapi_accepts_matching_scalar_response() -> None:
    openapi_doc = _openapi_doc(
        operation_id="countWidgets",
        status=200,
        schema={"type": "integer"},
    )
    response = httpx.Response(200, json=42)

    assert_response_matches_openapi(response, "countWidgets", 200, openapi_doc)


def test_assert_response_matches_openapi_fails_for_missing_operation_id() -> None:
    openapi_doc = _openapi_doc(
        operation_id="getWidget",
        status=200,
        schema={"type": "object"},
    )
    response = httpx.Response(200, json={})

    with pytest.raises(AssertionError, match="OpenAPI operation 'missingWidget' was not found"):
        assert_response_matches_openapi(response, "missingWidget", 200, openapi_doc)


def test_assert_response_matches_openapi_fails_for_undocumented_status() -> None:
    openapi_doc = _openapi_doc(
        operation_id="getWidget",
        status=200,
        schema={"type": "object"},
    )
    response = httpx.Response(201, json={})

    with pytest.raises(AssertionError, match="does not document status 201"):
        assert_response_matches_openapi(response, "getWidget", 201, openapi_doc)


def test_assert_response_matches_openapi_names_schema_path_on_validation_failure() -> None:
    openapi_doc = _openapi_doc(
        operation_id="getWidget",
        status=200,
        schema={
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        },
    )
    response = httpx.Response(200, json={})

    with pytest.raises(AssertionError) as exc_info:
        assert_response_matches_openapi(response, "getWidget", 200, openapi_doc)

    message = str(exc_info.value)
    assert "getWidget status 200" in message
    assert "#/paths/~1v1~1widgets/get/responses/200/content/application~1json/schema" in message
    assert "'name' is a required property" in message


def test_assert_response_matches_openapi_accepts_no_json_content_for_204() -> None:
    openapi_doc = _openapi_doc_without_content(operation_id="deleteWidget", status=204)
    response = httpx.Response(204, content=b"")

    assert_response_matches_openapi(response, "deleteWidget", 204, openapi_doc)


def test_assert_response_matches_openapi_validates_json_content_type() -> None:
    openapi_doc = _openapi_doc(
        operation_id="getWidget",
        status=200,
        schema={"type": "object"},
    )
    response = httpx.Response(
        200,
        content=b"{}",
        headers={"content-type": "text/plain"},
    )

    with pytest.raises(AssertionError, match="documented as application/json"):
        assert_response_matches_openapi(response, "getWidget", 200, openapi_doc)


def test_assert_response_matches_openapi_fails_for_missing_success_json_schema() -> None:
    openapi_doc = _openapi_doc(
        operation_id="getWidget",
        status=200,
        schema=None,
    )
    response = httpx.Response(200, json={})

    with pytest.raises(AssertionError, match="missing schema"):
        assert_response_matches_openapi(response, "getWidget", 200, openapi_doc)


def _openapi_doc(
    *,
    operation_id: str,
    status: int,
    schema: JsonValue | None,
    components: dict[str, JsonValue] | None = None,
) -> OpenApiDocument:
    media: dict[str, JsonValue] = {}
    if schema is not None:
        media["schema"] = schema

    return {
        "openapi": "3.1.0",
        "paths": {
            "/v1/widgets": {
                "get": {
                    "operationId": operation_id,
                    "responses": {
                        str(status): {
                            "description": "OK",
                            "content": {
                                "application/json": media,
                            },
                        },
                    },
                },
            },
        },
        "components": {
            "schemas": components or {},
        },
    }


def _openapi_doc_without_content(*, operation_id: str, status: int) -> OpenApiDocument:
    return {
        "openapi": "3.1.0",
        "paths": {
            "/v1/widgets": {
                "delete": {
                    "operationId": operation_id,
                    "responses": {
                        str(status): {
                            "description": "No Content",
                        },
                    },
                },
            },
        },
    }
