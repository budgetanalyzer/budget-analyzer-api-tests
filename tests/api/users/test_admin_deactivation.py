from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from api_tests.admin_deactivation import (
    AdminUserRecord,
    assert_sessions_revoked,
    deactivate_user_once,
    parse_timestamp,
    read_user,
    resolve_user_by_subject,
)
from api_tests.assertions import (
    JsonObject,
    array_field,
    assert_api_error,
    int_field,
    json_object_response,
    object_field,
    object_item,
    string_array_field,
    string_field,
)
from api_tests.auth import (
    AuthConfigurationError,
    ProvisionedDisposableTarget,
    acquire_disposable_browser_target,
)
from api_tests.browser_login import BrowserLoginError, login_with_browser
from api_tests.client import GatewayClient
from api_tests.config import EnvironmentConfig
from api_tests.local_credentials import (
    DisposableCredentialRecord,
    LocalCredentialError,
    bind_disposable_identity,
    require_owned_disposable_identity,
)
from api_tests.openapi import OpenApiDocument
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi
from api_tests.session import Session, ensure_distinct_sessions
from api_tests.session_gateway_assertions import (
    AuthenticatedIdentity,
    authenticated_identity,
    require_normal_identity,
)

_SCENARIO_ID = "public-admin-deactivation-revokes-sessions"


@pytest.mark.admin_deactivation
@pytest.mark.admin
@pytest.mark.authorization
@pytest.mark.mutation
@pytest.mark.destructive
@pytest.mark.openapi("deactivateUser")
@pytest.mark.forbidden_response("deactivateUser")
@pytest.mark.session_gateway_case("public-admin-deactivation-revokes-sessions")
def test_disposable_user_deactivation_revokes_every_public_session(
    admin_gateway_client: GatewayClient,
    gateway_client: GatewayClient,
    distinct_admin_user_identities: None,
    primary_identity: AuthenticatedIdentity,
    secondary_identity: AuthenticatedIdentity,
    admin_identity: AuthenticatedIdentity,
    env_config: EnvironmentConfig,
    request_log_path: Path | None,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    assert distinct_admin_user_identities is None
    reusable = tuple(
        resolve_user_by_subject(admin_gateway_client, identity.subject, openapi_snapshot)
        for identity in (primary_identity, secondary_identity, admin_identity)
    )
    reusable_subjects = frozenset(record.subject for record in reusable)
    reusable_user_ids = frozenset(record.id for record in reusable)
    reusable_role_ids = {record.id: record.role_ids for record in reusable}

    target = _acquire_target(env_config, run_state)
    first_session = target.first_session
    with GatewayClient(
        env_config,
        artifact_path=request_log_path,
        session=first_session,
    ) as first_client:
        target_identity = authenticated_identity(
            first_client.raw_request("GET", "/auth/v1/user"),
            session_gateway_openapi_snapshot,
        )
    require_normal_identity(target_identity, label="disposable target")
    target_user = resolve_user_by_subject(
        admin_gateway_client,
        target_identity.subject,
        openapi_snapshot,
    )
    _require_active_normal_target(target_user, target_identity)
    bound_record = _bind_target(
        env_config,
        target.record,
        run_state,
        target_identity,
        target_user,
        reusable_subjects,
        reusable_user_ids,
    )

    second_session = login_with_browser(env_config, bound_record.credentials)
    ensure_distinct_sessions(first_session, second_session)
    target_sessions = (first_session, second_session)
    _assert_target_sessions_active(
        env_config,
        target_sessions,
        target_identity,
        target_user,
        request_log_path=request_log_path,
        openapi_snapshot=openapi_snapshot,
        session_gateway_openapi_snapshot=session_gateway_openapi_snapshot,
    )

    require_owned_disposable_identity(
        bound_record,
        current_run_id=run_state.run_id,
        current_scenario_id=_SCENARIO_ID,
        subject=target_identity.subject,
        user_id=target_user.id,
        reusable_subjects=reusable_subjects,
        reusable_user_ids=reusable_user_ids,
    )
    denied = gateway_client.api_request(
        "POST",
        f"/v1/users/{target_user.id}/deactivate",
    )
    assert_response_matches_openapi(
        denied,
        "deactivateUser",
        httpx.codes.FORBIDDEN,
        openapi_snapshot,
    )
    assert_api_error(denied, httpx.codes.FORBIDDEN, error_type="FORBIDDEN")

    unchanged, unchanged_body = read_user(
        admin_gateway_client,
        target_user.id,
        openapi_snapshot,
    )
    _assert_target_unchanged_after_denial(unchanged, unchanged_body, target_user)
    _assert_target_sessions_active(
        env_config,
        target_sessions,
        target_identity,
        target_user,
        request_log_path=request_log_path,
        openapi_snapshot=openapi_snapshot,
        session_gateway_openapi_snapshot=session_gateway_openapi_snapshot,
    )

    require_owned_disposable_identity(
        bound_record,
        current_run_id=run_state.run_id,
        current_scenario_id=_SCENARIO_ID,
        subject=target_identity.subject,
        user_id=target_user.id,
        reusable_subjects=reusable_subjects,
        reusable_user_ids=reusable_user_ids,
    )
    request_started_at = datetime.now(UTC)
    result = deactivate_user_once(admin_gateway_client, target_user.id, openapi_snapshot)
    response_received_at = datetime.now(UTC)
    if (
        result.user_id != target_user.id
        or result.status != "DEACTIVATED"
        or result.roles_removed != len(target_user.role_ids)
        or result.sessions_revoked is not True
    ):
        raise AssertionError("initial deactivation result did not match the owned target state")

    deactivated, deactivated_body = read_user(
        admin_gateway_client,
        target_user.id,
        openapi_snapshot,
    )
    _assert_deactivated_audit(
        deactivated,
        deactivated_body,
        admin_user_id=reusable[2].id,
        request_started_at=request_started_at,
        response_received_at=response_received_at,
    )
    original_audit = (deactivated.deactivated_at, deactivated.deactivated_by_id)
    _assert_user_filter_membership(
        admin_gateway_client,
        target_user.id,
        openapi_snapshot,
        expected_status="DEACTIVATED",
    )

    assert_sessions_revoked(
        env_config,
        target_sessions,
        request_log_path=request_log_path,
        session_gateway_openapi_snapshot=session_gateway_openapi_snapshot,
        openapi_snapshot=openapi_snapshot,
    )

    repeated = deactivate_user_once(admin_gateway_client, target_user.id, openapi_snapshot)
    if (
        repeated.user_id != target_user.id
        or repeated.status != "DEACTIVATED"
        or repeated.roles_removed != 0
        or repeated.sessions_revoked is not True
    ):
        raise AssertionError("repeat deactivation was not idempotent")
    repeated_user, repeated_body = read_user(
        admin_gateway_client,
        target_user.id,
        openapi_snapshot,
    )
    if (repeated_user.deactivated_at, repeated_user.deactivated_by_id) != original_audit:
        raise AssertionError("repeat deactivation rewrote the original audit actor or time")
    if repeated_user.role_ids or string_array_field(repeated_body, "roleIds"):
        raise AssertionError("repeat deactivation restored or retained target roles")
    _assert_user_filter_membership(
        admin_gateway_client,
        target_user.id,
        openapi_snapshot,
        expected_status="DEACTIVATED",
    )

    _assert_missing_target_deactivation(
        admin_gateway_client,
        run_state,
        openapi_snapshot,
    )
    for reusable_user in reusable:
        current, _ = read_user(admin_gateway_client, reusable_user.id, openapi_snapshot)
        if current.status != "ACTIVE" or current.role_ids != reusable_role_ids[current.id]:
            raise AssertionError("deactivation changed a reusable test identity")


def _acquire_target(
    config: EnvironmentConfig,
    run_state: RunState,
) -> ProvisionedDisposableTarget:
    try:
        return acquire_disposable_browser_target(config, run_state.run_id, _SCENARIO_ID)
    except (AuthConfigurationError, BrowserLoginError) as exc:
        pytest.fail(f"disposable target provisioning prerequisite failed: {exc}", pytrace=False)


def _bind_target(
    config: EnvironmentConfig,
    record: DisposableCredentialRecord,
    run_state: RunState,
    identity: AuthenticatedIdentity,
    user: AdminUserRecord,
    reusable_subjects: frozenset[str],
    reusable_user_ids: frozenset[str],
) -> DisposableCredentialRecord:
    try:
        return bind_disposable_identity(
            config,
            record,
            current_run_id=run_state.run_id,
            current_scenario_id=_SCENARIO_ID,
            subject=identity.subject,
            user_id=user.id,
            reusable_subjects=reusable_subjects,
            reusable_user_ids=reusable_user_ids,
        )
    except LocalCredentialError as exc:
        pytest.fail(f"disposable target ownership check failed: {exc}", pytrace=False)


def _require_active_normal_target(
    user: AdminUserRecord,
    identity: AuthenticatedIdentity,
) -> None:
    if user.status != "ACTIVE" or user.deactivated_at is not None:
        raise AssertionError("fresh disposable target is not active")
    if user.role_ids != identity.roles or "USER" not in user.role_ids or "ADMIN" in user.role_ids:
        raise AssertionError("fresh disposable target does not have the expected normal-user roles")


def _assert_target_sessions_active(
    config: EnvironmentConfig,
    sessions: tuple[Session, Session],
    identity: AuthenticatedIdentity,
    user: AdminUserRecord,
    *,
    request_log_path: Path | None,
    openapi_snapshot: OpenApiDocument,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> None:
    for session in sessions:
        with GatewayClient(config, artifact_path=request_log_path, session=session) as client:
            current_identity = authenticated_identity(
                client.raw_request("GET", "/auth/v1/user"),
                session_gateway_openapi_snapshot,
            )
            if current_identity.subject != identity.subject:
                raise AssertionError("target sessions resolved to different public subjects")

            heartbeat = client.raw_request("GET", "/auth/v1/session")
            assert_response_matches_openapi(
                heartbeat,
                "getSessionStatus",
                httpx.codes.OK,
                session_gateway_openapi_snapshot,
            )
            heartbeat_body = json_object_response(heartbeat, httpx.codes.OK)
            if string_field(heartbeat_body, "userId") != user.id:
                raise AssertionError("target heartbeat resolved to a different canonical user")
            if frozenset(string_array_field(heartbeat_body, "roles")) != user.role_ids:
                raise AssertionError("target heartbeat roles differ from canonical user roles")
            int_field(heartbeat_body, "expiresAt")

            protected_read = client.api_request("GET", "/v1/currencies")
            assert_response_matches_openapi(
                protected_read,
                "getAll",
                httpx.codes.OK,
                openapi_snapshot,
            )


def _assert_target_unchanged_after_denial(
    current: AdminUserRecord,
    body: JsonObject,
    original: AdminUserRecord,
) -> None:
    if current != original:
        raise AssertionError("normal-user deactivation denial changed target state")
    if any(body.get(field) is not None for field in ("deactivatedAt", "deactivatedBy")):
        raise AssertionError("normal-user denial added deactivation audit state")


def _assert_deactivated_audit(
    user: AdminUserRecord,
    body: JsonObject,
    *,
    admin_user_id: str,
    request_started_at: datetime,
    response_received_at: datetime,
) -> None:
    if user.status != "DEACTIVATED" or user.role_ids:
        raise AssertionError("deactivated target retained active status or roles")
    if user.deactivated_at is None or user.deactivated_by_id != admin_user_id:
        raise AssertionError("deactivated target audit fields do not identify the admin operation")
    deactivated_at = parse_timestamp(user.deactivated_at)
    if not request_started_at <= deactivated_at <= response_received_at:
        raise AssertionError("deactivation audit timestamp falls outside the operation window")
    actor = object_field(body, "deactivatedBy")
    if string_field(actor, "id") != admin_user_id:
        raise AssertionError("deactivation audit actor did not match the reusable admin")
    if body.get("deletedAt") is not None or body.get("deletedBy") is not None:
        raise AssertionError("deactivation unexpectedly soft-deleted the target")


def _assert_user_filter_membership(
    client: GatewayClient,
    user_id: str,
    openapi_snapshot: OpenApiDocument,
    *,
    expected_status: str,
) -> None:
    expected = client.api_request(
        "GET",
        "/v1/users",
        params={"id": user_id, "status": expected_status},
    )
    assert_response_matches_openapi(expected, "getUsers", httpx.codes.OK, openapi_snapshot)
    expected_body = json_object_response(expected, httpx.codes.OK)
    rows = array_field(expected_body, "content")
    if len(rows) != 1 or string_field(object_item(rows[0]), "id") != user_id:
        raise AssertionError("target was absent from its expected status filter")

    other_status = "ACTIVE" if expected_status == "DEACTIVATED" else "DEACTIVATED"
    excluded = client.api_request(
        "GET",
        "/v1/users",
        params={"id": user_id, "status": other_status},
    )
    assert_response_matches_openapi(excluded, "getUsers", httpx.codes.OK, openapi_snapshot)
    excluded_body = json_object_response(excluded, httpx.codes.OK)
    if array_field(excluded_body, "content") or int_field(
        object_field(excluded_body, "metadata"), "totalElements"
    ):
        raise AssertionError("target appeared in the wrong status filter")


def _assert_missing_target_deactivation(
    client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    missing_id = f"usr_{run_state.run_id}_{secrets.token_hex(8)}_missing"
    confirmed_missing = client.api_request("GET", f"/v1/users/{missing_id}")
    assert_response_matches_openapi(
        confirmed_missing,
        "getUser",
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    assert_api_error(confirmed_missing, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")

    response = client.api_request("POST", f"/v1/users/{missing_id}/deactivate")
    assert_response_matches_openapi(
        response,
        "deactivateUser",
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    assert_api_error(response, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")
