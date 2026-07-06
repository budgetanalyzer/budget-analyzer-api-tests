from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import httpx

from api_tests.client import GatewayClient

type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
type OpenApiDocument = Mapping[str, JsonValue]

HTTP_METHODS: frozenset[str] = frozenset({"get", "post", "put", "patch", "delete"})
type HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


@dataclass(frozen=True, slots=True)
class OpenApiOperation:
    operation_id: str
    method: HttpMethod
    path: str


def load_snapshot(path: Path) -> OpenApiDocument:
    with path.open("r", encoding="utf-8") as snapshot_file:
        raw_document = json.load(snapshot_file)

    if not isinstance(raw_document, dict):
        raise ValueError(f"OpenAPI snapshot must contain a JSON object: {path}")
    return cast(dict[str, JsonValue], raw_document)


def fetch_live_openapi(client: GatewayClient, openapi_path: str) -> OpenApiDocument:
    response = client.raw_request("GET", openapi_path)
    if response.status_code != httpx.codes.OK:
        raise ValueError(
            f"failed to fetch OpenAPI document from {openapi_path}: HTTP {response.status_code}"
        )

    raw_document = response.json()
    if not isinstance(raw_document, dict):
        raise ValueError(f"OpenAPI response must contain a JSON object: {openapi_path}")
    return cast(dict[str, JsonValue], raw_document)


def iter_operations(openapi_doc: OpenApiDocument) -> Iterator[OpenApiOperation]:
    paths = _required_mapping(openapi_doc, "paths", "OpenAPI document")
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                raise ValueError(f"OpenAPI operation {method.upper()} {path} must be an object")

            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id.strip():
                raise ValueError(
                    f"OpenAPI operation {method.upper()} {path} is missing operationId"
                )

            yield OpenApiOperation(
                operation_id=operation_id,
                method=_normalize_method(method),
                path=path,
            )


def operation_response_schema(
    openapi_doc: OpenApiDocument,
    operation_id: str,
    status: int | str,
) -> JsonValue | None:
    status_key = str(status)
    for operation in _iter_raw_operations(openapi_doc):
        if operation.get("operationId") != operation_id:
            continue

        responses = operation.get("responses")
        if not isinstance(responses, dict):
            return None
        response = responses.get(status_key) or responses.get("default")
        if not isinstance(response, dict):
            return None
        content = response.get("content")
        if not isinstance(content, dict):
            return None
        media = content.get("application/json")
        if not isinstance(media, dict):
            return None
        return media.get("schema")

    raise ValueError(f"OpenAPI operation not found: {operation_id}")


def _iter_raw_operations(openapi_doc: OpenApiDocument) -> Iterator[Mapping[str, JsonValue]]:
    paths = _required_mapping(openapi_doc, "paths", "OpenAPI document")
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method in HTTP_METHODS and isinstance(operation, dict):
                yield operation


def _required_mapping(
    parent: Mapping[str, JsonValue],
    key: str,
    context: str,
) -> Mapping[str, JsonValue]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{context} must contain object field {key!r}")
    return value


def _normalize_method(method: str) -> HttpMethod:
    normalized = method.upper()
    if normalized not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError(f"unsupported OpenAPI method: {method}")
    return cast(HttpMethod, normalized)
