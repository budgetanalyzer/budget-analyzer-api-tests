from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import httpx
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]  # missing stubs
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]  # missing stubs
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from api_tests.openapi import HTTP_METHODS, JsonValue, OpenApiDocument, operation_response_schema

_OPENAPI_DOCUMENT_URI = "urn:budget-analyzer-api-tests:openapi-document"


def assert_response_matches_openapi(
    response: httpx.Response,
    operation_id: str,
    expected_status: int,
    openapi_doc: OpenApiDocument,
) -> None:
    assert response.status_code == expected_status, (
        f"{operation_id} expected HTTP {expected_status}, got HTTP {response.status_code}: "
        f"{response.text}"
    )

    documented_response = _documented_response(openapi_doc, operation_id, expected_status)
    json_content = _json_content(documented_response.response, documented_response.path)
    if json_content is None:
        if expected_status == httpx.codes.NO_CONTENT:
            assert response.content == b"", (
                f"{operation_id} status {expected_status} is documented with no JSON content, "
                f"but response body was {response.content!r}"
            )
        return

    media_type = response.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    assert media_type == "application/json", (
        f"{operation_id} status {expected_status} is documented as application/json at "
        f"{json_content.path}, but response content-type was "
        f"{response.headers.get('content-type')!r}"
    )

    schema = _lookup_response_schema(openapi_doc, operation_id, expected_status)
    schema_path = f"{json_content.path}/schema"
    if schema is None:
        if _is_success_status(expected_status):
            raise AssertionError(
                f"{operation_id} status {expected_status} documents application/json at "
                f"{json_content.path} but is missing schema {schema_path}"
            )
        return
    if not isinstance(schema, bool | dict):
        raise AssertionError(
            f"{operation_id} status {expected_status} has invalid OpenAPI schema at "
            f"{schema_path}: expected object or boolean, got {type(schema).__name__}"
        )

    try:
        response_body: object = response.json()
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{operation_id} status {expected_status} response did not contain valid JSON "
            f"for schema {schema_path}: {exc}"
        ) from exc

    registry = Registry().with_resource(
        _OPENAPI_DOCUMENT_URI,
        Resource.from_contents(
            cast(Any, openapi_doc),
            default_specification=DRAFT202012,
        ),
    )
    validator = Draft202012Validator(
        {"$ref": f"{_OPENAPI_DOCUMENT_URI}{schema_path}"},
        registry=registry,
    )
    errors = sorted(validator.iter_errors(cast(Any, response_body)), key=str)
    if errors:
        first_error = errors[0]
        raise AssertionError(
            f"OpenAPI schema validation failed for {operation_id} status {expected_status} "
            f"at {schema_path}: {_format_validation_error(first_error)}"
        )


@dataclass(frozen=True, slots=True)
class _DocumentedResponse:
    response: Mapping[str, JsonValue]
    path: str


@dataclass(frozen=True, slots=True)
class _JsonContent:
    content: Mapping[str, JsonValue]
    path: str


def _lookup_response_schema(
    openapi_doc: OpenApiDocument,
    operation_id: str,
    expected_status: int,
) -> JsonValue | None:
    try:
        return operation_response_schema(openapi_doc, operation_id, expected_status)
    except ValueError as exc:
        raise AssertionError(
            f"OpenAPI operation {operation_id!r} was not found while looking up "
            f"status {expected_status} response schema"
        ) from exc


def _documented_response(
    openapi_doc: OpenApiDocument,
    operation_id: str,
    expected_status: int,
) -> _DocumentedResponse:
    paths = openapi_doc.get("paths")
    if not isinstance(paths, dict):
        raise AssertionError("OpenAPI document must contain object field 'paths'")

    for openapi_path, path_item in paths.items():
        if not isinstance(openapi_path, str) or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            if operation.get("operationId") != operation_id:
                continue

            operation_path = f"#/paths/{_escape_pointer(openapi_path)}/{method}"
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                raise AssertionError(
                    f"OpenAPI operation {operation_id!r} does not document responses "
                    f"at {operation_path}/responses"
                )

            status_key = str(expected_status)
            response_key = status_key if status_key in responses else "default"
            response_entry = responses.get(response_key)
            if not isinstance(response_entry, dict):
                raise AssertionError(
                    f"OpenAPI operation {operation_id!r} does not document status "
                    f"{expected_status} at {operation_path}/responses/{_escape_pointer(status_key)}"
                )
            return _DocumentedResponse(
                response=cast(Mapping[str, JsonValue], response_entry),
                path=f"{operation_path}/responses/{_escape_pointer(response_key)}",
            )

    raise AssertionError(
        f"OpenAPI operation {operation_id!r} was not found while checking status {expected_status}"
    )


def _json_content(
    response_entry: Mapping[str, JsonValue],
    response_path: str,
) -> _JsonContent | None:
    content = response_entry.get("content")
    if not isinstance(content, dict):
        return None

    media = content.get("application/json")
    if not isinstance(media, dict):
        return None

    return _JsonContent(
        content=cast(Mapping[str, JsonValue], media),
        path=f"{response_path}/content/application~1json",
    )


def _format_validation_error(error: ValidationError) -> str:
    response_path = _format_path(error.path)
    schema_error_path = _format_path(error.schema_path)
    return f"{error.message} (response path: {response_path}, schema path: {schema_error_path})"


def _format_path(path: Iterable[object]) -> str:
    segments = tuple(path)
    if not segments:
        return "$"
    return "$." + ".".join(str(segment) for segment in segments)


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _is_success_status(status: int) -> bool:
    return 200 <= status < 300
