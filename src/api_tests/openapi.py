from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote

import httpx
import yaml

from api_tests.client import GatewayClient

type JsonValue = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None
type OpenApiDocument = Mapping[str, JsonValue]
type OpenApiContractKey = Literal["unified", "session_gateway"]

HTTP_METHODS: frozenset[str] = frozenset({"get", "post", "put", "patch", "delete"})
type HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
type RequestSafetyMarker = Literal["readonly", "production_safe", "mutation", "destructive"]
type ForbiddenResponseDeferralReason = Literal[
    "no_restricted_normal_user_scenario",
    "admin_identity_and_safety_plan_required",
]

FORBIDDEN_RESPONSE_DEFERRAL_REASONS: frozenset[str] = frozenset(
    {
        "no_restricted_normal_user_scenario",
        "admin_identity_and_safety_plan_required",
    }
)
_PATH_PARAMETER_PATTERN = re.compile(r"{([^{}]+)}")
_UUID_PATH_FALLBACK = "00000000-0000-4000-8000-000000000001"
_DESTRUCTIVE_UNAUTHENTICATED_OPERATION_IDS: frozenset[str] = frozenset(
    {
        "batchImportTransactions",
        "bulkDeleteTransactions",
        "deactivateUser",
        "deleteTransaction",
        "deleteView",
        "hideFormat",
        "importLatestExchangeRates",
        "updateViewTransactions",
    }
)


@dataclass(frozen=True, slots=True)
class OpenApiOperation:
    operation_id: str
    method: HttpMethod
    path: str
    contract: OpenApiContractKey = "unified"


@dataclass(frozen=True, slots=True)
class OpenApiRequestCase:
    operation_id: str
    method: HttpMethod
    path: str
    contract: OpenApiContractKey = "unified"

    @property
    def case_id(self) -> str:
        return self.operation_id


@dataclass(frozen=True, slots=True)
class ForbiddenResponseDeferral:
    operation_id: str
    method: HttpMethod
    path: str
    reason: ForbiddenResponseDeferralReason


@dataclass(frozen=True, slots=True)
class _OpenApiOperationEntry:
    operation: OpenApiOperation
    path_item: Mapping[str, JsonValue]
    raw_operation: Mapping[str, JsonValue]


def load_snapshot(path: Path) -> OpenApiDocument:
    with path.open("r", encoding="utf-8") as snapshot_file:
        if path.suffix.lower() == ".json":
            raw_document = json.load(snapshot_file)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            raw_document = yaml.safe_load(snapshot_file)
        else:
            raise ValueError(f"OpenAPI snapshot must be JSON or YAML: {path}")

    if not isinstance(raw_document, dict):
        raise ValueError(f"OpenAPI snapshot must contain an object: {path}")
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


def iter_operations(
    openapi_doc: OpenApiDocument,
    *,
    contract: OpenApiContractKey = "unified",
) -> Iterator[OpenApiOperation]:
    for entry in _validated_operation_entries(openapi_doc, contract=contract):
        yield entry.operation


def operations_documenting_response(
    openapi_doc: OpenApiDocument,
    status: int | str,
    *,
    contract: OpenApiContractKey = "unified",
) -> tuple[OpenApiOperation, ...]:
    status_key = str(status)
    operations = [
        entry.operation
        for entry in _validated_operation_entries(openapi_doc, contract=contract)
        if _documents_response(entry.raw_operation, status_key)
    ]
    return tuple(sorted(operations, key=_operation_sort_key))


def build_operation_request_cases(
    openapi_doc: OpenApiDocument,
    *,
    contract: OpenApiContractKey = "unified",
    required_response_statuses: Iterable[int | str] = (),
) -> tuple[OpenApiRequestCase, ...]:
    status_keys = tuple(dict.fromkeys(str(status) for status in required_response_statuses))
    cases: list[OpenApiRequestCase] = []
    for entry in _validated_operation_entries(openapi_doc, contract=contract):
        for status_key in status_keys:
            _require_json_response_schema(entry, status_key)
        cases.append(
            OpenApiRequestCase(
                contract=entry.operation.contract,
                operation_id=entry.operation.operation_id,
                method=entry.operation.method,
                path=_materialize_operation_path(entry),
            )
        )
    return tuple(sorted(cases, key=lambda case: (case.path, case.method, case.operation_id)))


def unauthenticated_request_safety_markers(
    case: OpenApiRequestCase,
) -> tuple[RequestSafetyMarker, ...]:
    if case.method == "GET":
        return ("readonly", "production_safe")

    if case.method == "DELETE" or case.operation_id in _DESTRUCTIVE_UNAUTHENTICATED_OPERATION_IDS:
        return ("mutation", "destructive")
    return ("mutation",)


def execute_openapi_request_case(
    client: GatewayClient,
    case: OpenApiRequestCase,
) -> httpx.Response:
    return client.api_request(case.method, case.path)


def load_forbidden_response_deferrals(path: Path) -> tuple[ForbiddenResponseDeferral, ...]:
    with path.open("r", encoding="utf-8") as manifest_file:
        raw_manifest = yaml.safe_load(manifest_file)

    if not isinstance(raw_manifest, dict):
        raise ValueError(f"forbidden-response manifest must contain a YAML mapping: {path}")
    raw_deferrals = raw_manifest.get("deferred_forbidden_responses")
    if not isinstance(raw_deferrals, list):
        raise ValueError(
            "forbidden-response manifest must contain list field "
            f"'deferred_forbidden_responses': {path}"
        )

    deferrals: list[ForbiddenResponseDeferral] = []
    for index, raw_deferral in enumerate(raw_deferrals):
        if not isinstance(raw_deferral, dict):
            raise ValueError(f"forbidden-response deferral #{index + 1} must be a mapping: {path}")
        operation_id = raw_deferral.get("operationId")
        method = raw_deferral.get("method")
        operation_path = raw_deferral.get("path")
        reason = raw_deferral.get("reason")
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError(f"forbidden-response deferral #{index + 1} is missing operationId")
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(
                f"forbidden-response deferral {operation_id} has unsupported method: {method}"
            )
        if not isinstance(operation_path, str) or not operation_path.startswith("/"):
            raise ValueError(f"forbidden-response deferral {operation_id} has invalid path")
        if reason not in FORBIDDEN_RESPONSE_DEFERRAL_REASONS:
            raise ValueError(
                f"forbidden-response deferral {operation_id} has unsupported reason: {reason}"
            )
        deferrals.append(
            ForbiddenResponseDeferral(
                operation_id=operation_id,
                method=method,
                path=operation_path,
                reason=cast(ForbiddenResponseDeferralReason, reason),
            )
        )
    return tuple(deferrals)


def validate_forbidden_response_deferrals(
    openapi_doc: OpenApiDocument,
    deferrals: Iterable[ForbiddenResponseDeferral],
    *,
    deferred_admin_operation_ids: Iterable[str],
) -> None:
    operations = tuple(iter_operations(openapi_doc))
    operations_by_id = {operation.operation_id: operation for operation in operations}
    deferred_admin_ids = set(deferred_admin_operation_ids)
    documented_forbidden_ids = {
        operation.operation_id for operation in operations_documenting_response(openapi_doc, 403)
    }
    all_operation_ids = set(operations_by_id)
    if documented_forbidden_ids != all_operation_ids:
        missing = sorted(all_operation_ids - documented_forbidden_ids)
        raise ValueError(
            "OpenAPI operations missing documented 403 responses: " + ", ".join(missing)
        )

    seen_ids: set[str] = set()
    for deferral in deferrals:
        if deferral.operation_id in seen_ids:
            raise ValueError(
                f"duplicate forbidden-response deferral operationId: {deferral.operation_id}"
            )
        seen_ids.add(deferral.operation_id)
        operation = operations_by_id.get(deferral.operation_id)
        if operation is None:
            raise ValueError(
                "stale forbidden-response deferral is absent from OpenAPI: "
                f"{deferral.operation_id} ({deferral.method} {deferral.path})"
            )
        if operation.method != deferral.method or operation.path != deferral.path:
            raise ValueError(
                f"stale forbidden-response method/path for {deferral.operation_id}: manifest has "
                f"{deferral.method} {deferral.path}, OpenAPI has "
                f"{operation.method} {operation.path}"
            )
        expected_reason: ForbiddenResponseDeferralReason = (
            "admin_identity_and_safety_plan_required"
            if deferral.operation_id in deferred_admin_ids
            else "no_restricted_normal_user_scenario"
        )
        if deferral.reason != expected_reason:
            raise ValueError(
                f"forbidden-response deferral reason mismatch for {deferral.operation_id}: "
                f"expected {expected_reason}, found {deferral.reason}"
            )

    missing_deferrals = sorted(all_operation_ids - seen_ids)
    if missing_deferrals:
        raise ValueError(
            "OpenAPI operations missing forbidden-response deferrals: "
            + ", ".join(missing_deferrals)
        )


def _validated_operation_entries(
    openapi_doc: OpenApiDocument,
    *,
    contract: OpenApiContractKey,
) -> tuple[_OpenApiOperationEntry, ...]:
    paths = _required_mapping(openapi_doc, "paths", "OpenAPI document")
    entries: list[_OpenApiOperationEntry] = []
    operations_by_id: dict[str, OpenApiOperation] = {}
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

            parsed_operation = OpenApiOperation(
                contract=contract,
                operation_id=operation_id,
                method=_normalize_method(method),
                path=path,
            )
            previous = operations_by_id.get(operation_id)
            if previous is not None:
                raise ValueError(
                    "duplicate OpenAPI operationId "
                    f"{operation_id!r}: {previous.method} {previous.path} and "
                    f"{parsed_operation.method} {parsed_operation.path}"
                )
            operations_by_id[operation_id] = parsed_operation
            entries.append(
                _OpenApiOperationEntry(
                    operation=parsed_operation,
                    path_item=path_item,
                    raw_operation=operation,
                )
            )
    return tuple(entries)


def _documents_response(operation: Mapping[str, JsonValue], status_key: str) -> bool:
    responses = operation.get("responses")
    return isinstance(responses, dict) and status_key in responses


def _require_json_response_schema(entry: _OpenApiOperationEntry, status_key: str) -> None:
    context = _operation_context(entry.operation)
    responses = entry.raw_operation.get("responses")
    if not isinstance(responses, dict) or status_key not in responses:
        raise ValueError(f"{context} is missing documented {status_key} response")
    response = responses[status_key]
    if not isinstance(response, dict):
        raise ValueError(f"{context} response {status_key} must be an object")
    content = response.get("content")
    if not isinstance(content, dict):
        raise ValueError(f"{context} response {status_key} is missing application/json content")
    media = content.get("application/json")
    if not isinstance(media, dict) or not isinstance(media.get("schema"), dict):
        raise ValueError(f"{context} response {status_key} is missing application/json schema")


def _materialize_operation_path(entry: _OpenApiOperationEntry) -> str:
    operation = entry.operation
    context = _operation_context(operation)
    placeholders = _PATH_PARAMETER_PATTERN.findall(operation.path)
    if operation.path.count("{") != len(placeholders) or operation.path.count("}") != len(
        placeholders
    ):
        raise ValueError(f"{context} has unsupported path template: {operation.path}")

    seen_placeholders: set[str] = set()
    for placeholder in placeholders:
        if placeholder in seen_placeholders:
            raise ValueError(f"{context} has duplicate path parameter {placeholder!r}")
        seen_placeholders.add(placeholder)

    parameters = _path_parameters(entry)
    for placeholder in placeholders:
        if placeholder not in parameters:
            raise ValueError(f"{context} path parameter {placeholder!r} is missing a declaration")
    for parameter_name in parameters:
        if parameter_name not in seen_placeholders:
            raise ValueError(
                f"{context} path parameter {parameter_name!r} does not match the path template"
            )

    materialized = operation.path
    for placeholder in placeholders:
        value = _path_parameter_value(operation, placeholder, parameters[placeholder])
        materialized = materialized.replace("{" + placeholder + "}", value, 1)
    return materialized


def _path_parameters(entry: _OpenApiOperationEntry) -> dict[str, Mapping[str, JsonValue]]:
    context = _operation_context(entry.operation)
    parameters: dict[str, Mapping[str, JsonValue]] = {}
    for owner, raw_parameters in (
        ("path item", entry.path_item.get("parameters", [])),
        ("operation", entry.raw_operation.get("parameters", [])),
    ):
        if not isinstance(raw_parameters, list):
            raise ValueError(f"{context} {owner} parameters must be an array")
        for index, parameter in enumerate(raw_parameters):
            if not isinstance(parameter, dict):
                raise ValueError(f"{context} {owner} parameter #{index + 1} must be an object")
            if parameter.get("in") != "path":
                continue
            name = parameter.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError(f"{context} {owner} path parameter #{index + 1} is missing name")
            if name in parameters:
                raise ValueError(f"{context} has duplicate path parameter {name!r}")
            if parameter.get("required") is not True:
                raise ValueError(f"{context} path parameter {name!r} must be required")
            parameters[name] = parameter
    return parameters


def _path_parameter_value(
    operation: OpenApiOperation,
    name: str,
    parameter: Mapping[str, JsonValue],
) -> str:
    context = _operation_context(operation)
    schema = parameter.get("schema")
    if not isinstance(schema, dict):
        raise ValueError(f"{context} path parameter {name!r} is missing a schema")

    example = parameter.get("example")
    if example is None:
        example = schema.get("example")
    if example is not None:
        if isinstance(example, bool) or not isinstance(example, str | int | float):
            raise ValueError(f"{context} path parameter {name!r} has an unsupported example")
        value = str(example)
        if not value:
            raise ValueError(f"{context} path parameter {name!r} has an empty example")
        return quote(value, safe="")

    schema_type = schema.get("type")
    schema_format = schema.get("format")
    if schema_type == "integer":
        return "1"
    if schema_type == "string" and schema_format == "uuid":
        return _UUID_PATH_FALLBACK
    if schema_type == "string":
        return "example"
    raise ValueError(
        f"{context} path parameter {name!r} has unsupported schema type/format: "
        f"{schema_type}/{schema_format}"
    )


def _operation_context(operation: OpenApiOperation) -> str:
    return f"OpenAPI operation {operation.operation_id!r} ({operation.method} {operation.path})"


def _operation_sort_key(operation: OpenApiOperation) -> tuple[str, HttpMethod, str]:
    return operation.path, operation.method, operation.operation_id


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
