from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from api_tests.assertions import (
    JsonObject,
    array_field,
    assert_api_error,
    assert_cookie_cleared,
    bool_field,
    int_field,
    json_object_response,
    object_field,
    object_item,
    string_array_field,
    string_field,
)
from api_tests.client import GatewayClient
from api_tests.config import EnvironmentConfig
from api_tests.openapi import OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi
from api_tests.session import Session


@dataclass(frozen=True, slots=True, repr=False)
class AdminUserRecord:
    id: str
    subject: str
    email: str
    display_name: str
    status: str
    role_ids: frozenset[str]
    created_at: str
    updated_at: str | None
    deactivated_at: str | None
    deactivated_by_id: str | None


@dataclass(frozen=True, slots=True, repr=False)
class UserDeactivationResult:
    user_id: str
    status: str
    roles_removed: int
    sessions_revoked: bool


class PartialDeactivationError(AssertionError):
    def __init__(self, observed_status: str) -> None:
        super().__init__(
            "deactivation returned 503 after its state transition; no retry was sent and the "
            f"retained target currently reports status {observed_status!r}"
        )
        self.observed_status = observed_status


def resolve_user_by_subject(
    client: GatewayClient,
    subject: str,
    openapi_snapshot: OpenApiDocument,
) -> AdminUserRecord:
    response = client.api_request("GET", "/v1/users", params={"idpSub": subject})
    assert_response_matches_openapi(response, "getUsers", httpx.codes.OK, openapi_snapshot)
    body = json_object_response(response, httpx.codes.OK)
    rows = array_field(body, "content")
    metadata = object_field(body, "metadata")
    if len(rows) != 1 or int_field(metadata, "totalElements") != 1:
        raise AssertionError("an exact public subject did not resolve to one canonical user")
    record = admin_user_record(object_item(rows[0]))
    if record.subject != subject:
        raise AssertionError("resolved user subject did not match the authenticated identity")
    return record


def read_user(
    client: GatewayClient,
    user_id: str,
    openapi_snapshot: OpenApiDocument,
) -> tuple[AdminUserRecord, JsonObject]:
    response = client.api_request("GET", f"/v1/users/{user_id}")
    assert_response_matches_openapi(response, "getUser", httpx.codes.OK, openapi_snapshot)
    body = json_object_response(response, httpx.codes.OK)
    record = admin_user_record(body)
    if record.id != user_id:
        raise AssertionError("user detail returned a different canonical id")
    return record, body


def admin_user_record(body: JsonObject) -> AdminUserRecord:
    deactivated_by = body.get("deactivatedBy")
    deactivated_by_id: str | None = None
    if deactivated_by is not None:
        deactivated_by_id = string_field(object_item(deactivated_by), "id")
    return AdminUserRecord(
        id=string_field(body, "id"),
        subject=string_field(body, "idpSub"),
        email=string_field(body, "email"),
        display_name=string_field(body, "displayName"),
        status=string_field(body, "status"),
        role_ids=frozenset(string_array_field(body, "roleIds")),
        created_at=string_field(body, "createdAt"),
        updated_at=_optional_string(body, "updatedAt"),
        deactivated_at=_optional_string(body, "deactivatedAt"),
        deactivated_by_id=deactivated_by_id,
    )


def deactivate_user_once(
    client: GatewayClient,
    user_id: str,
    openapi_snapshot: OpenApiDocument,
) -> UserDeactivationResult:
    """Send exactly one deactivation attempt and expose a committed 503 state without retrying."""
    response = client.api_request("POST", f"/v1/users/{user_id}/deactivate")
    if response.status_code == httpx.codes.SERVICE_UNAVAILABLE:
        assert_response_matches_openapi(
            response,
            "deactivateUser",
            httpx.codes.SERVICE_UNAVAILABLE,
            openapi_snapshot,
        )
        assert_api_error(
            response,
            httpx.codes.SERVICE_UNAVAILABLE,
            error_type="SERVICE_UNAVAILABLE",
        )
        current, _ = read_user(client, user_id, openapi_snapshot)
        raise PartialDeactivationError(current.status)

    assert_response_matches_openapi(
        response,
        "deactivateUser",
        httpx.codes.OK,
        openapi_snapshot,
    )
    body = json_object_response(response, httpx.codes.OK)
    return UserDeactivationResult(
        user_id=string_field(body, "userId"),
        status=string_field(body, "status"),
        roles_removed=int_field(body, "rolesRemoved"),
        sessions_revoked=bool_field(body, "sessionsRevoked"),
    )


def assert_sessions_revoked(
    config: EnvironmentConfig,
    sessions: tuple[Session, ...],
    *,
    request_log_path: Path | None,
    session_gateway_openapi_snapshot: OpenApiDocument,
    openapi_snapshot: OpenApiDocument,
) -> None:
    for session in sessions:
        _assert_original_cookie_rejected(
            config,
            session,
            request_log_path=request_log_path,
            session_gateway_openapi_snapshot=session_gateway_openapi_snapshot,
            openapi_snapshot=openapi_snapshot,
        )


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AssertionError("audit timestamp was not timezone-aware")
    return parsed


def _assert_original_cookie_rejected(
    config: EnvironmentConfig,
    session: Session,
    *,
    request_log_path: Path | None,
    session_gateway_openapi_snapshot: OpenApiDocument,
    openapi_snapshot: OpenApiDocument,
) -> None:
    requests = (
        ("raw", "/auth/v1/user", "getCurrentUser", session_gateway_openapi_snapshot),
        ("api", "/v1/currencies", "getAll", openapi_snapshot),
        ("raw", "/auth/v1/session", "getSessionStatus", session_gateway_openapi_snapshot),
    )
    for request_kind, path, operation_id, snapshot in requests:
        with GatewayClient(config, artifact_path=request_log_path, session=session) as client:
            if request_kind == "api":
                response = client.api_request("GET", path)
            else:
                response = client.raw_request("GET", path)
        body = assert_api_error(
            response,
            httpx.codes.UNAUTHORIZED,
            error_type="UNAUTHORIZED",
        )
        if string_field(body, "message") != "Authentication required":
            raise AssertionError("revoked session returned an unexpected denial message")
        assert_response_matches_openapi(
            response,
            operation_id,
            httpx.codes.UNAUTHORIZED,
            snapshot,
        )
        if path == "/auth/v1/session":
            assert_cookie_cleared(response, config.session_cookie_name)


def _optional_string(body: JsonObject, field_name: str) -> str | None:
    value = body.get(field_name)
    if value is not None and not isinstance(value, str):
        raise AssertionError(f"optional field {field_name!r} had an invalid shape")
    return value
