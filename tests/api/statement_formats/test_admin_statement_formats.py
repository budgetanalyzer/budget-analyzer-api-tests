from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import httpx
import pytest

from api_tests.assertions import (
    JsonObject,
    array_field,
    assert_api_error,
    assert_no_content,
    bool_field,
    int_field,
    json_array_response,
    json_object_response,
    object_item,
    string_field,
)
from api_tests.builders.statement_formats import update_statement_format_request
from api_tests.client import GatewayClient
from api_tests.openapi import JsonValue, OpenApiDocument
from api_tests.resources import CreatedStatementFormat, create_statement_format
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi


@pytest.mark.openapi("listFormats")
@pytest.mark.openapi("createFormat")
@pytest.mark.openapi("getFormat")
@pytest.mark.openapi("updateFormat")
@pytest.mark.openapi("hideFormat")
@pytest.mark.openapi("unhideFormat")
@pytest.mark.admin
@pytest.mark.authorization
@pytest.mark.destructive
@pytest.mark.mutation
def test_admin_reads_and_updates_test_created_user_formats_with_caller_preferences(
    gateway_client: GatewayClient,
    secondary_gateway_client: GatewayClient,
    admin_gateway_client: GatewayClient,
    distinct_admin_user_identities: None,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    assert distinct_admin_user_identities is None
    primary = create_statement_format(
        gateway_client,
        run_state,
        "admin-primary-format",
        openapi_snapshot=openapi_snapshot,
    )
    secondary = create_statement_format(
        secondary_gateway_client,
        run_state,
        "admin-secondary-format",
        openapi_snapshot=openapi_snapshot,
    )
    _assert_created_user_format(primary)
    _assert_created_user_format(secondary)
    primary_owner = string_field(primary.body, "ownerId")
    secondary_owner = string_field(secondary.body, "ownerId")
    if primary_owner == secondary_owner:
        raise AssertionError("distinct normal identities created formats with one owner")

    primary_visible = _list_formats(
        gateway_client,
        include_hidden=False,
        openapi_snapshot=openapi_snapshot,
    )
    secondary_visible = _list_formats(
        secondary_gateway_client,
        include_hidden=False,
        openapi_snapshot=openapi_snapshot,
    )
    _require_format_visibility(primary_visible, present=(primary.id,), absent=(secondary.id,))
    _require_format_visibility(secondary_visible, present=(secondary.id,), absent=(primary.id,))

    admin_visible = _list_formats(
        admin_gateway_client,
        include_hidden=False,
        openapi_snapshot=openapi_snapshot,
    )
    _require_format_visibility(admin_visible, present=(primary.id, secondary.id))
    persisted_baselines: dict[int, JsonObject] = {}
    for created in (primary, secondary):
        listed = _find_format(admin_visible, created.id)
        if bool_field(listed, "hidden"):
            raise AssertionError("an owner preference leaked into the admin caller's format list")
        detail = _get_format(admin_gateway_client, created.id, openapi_snapshot)
        _assert_format_invariants(detail, created.body)
        persisted_baselines[created.id] = detail

    _assert_not_found(
        admin_gateway_client.api_request(
            "POST",
            f"/v1/statement-formats/{primary.id}/hide",
        ),
        "hideFormat",
        openapi_snapshot,
    )

    hide_response = gateway_client.api_request(
        "POST",
        f"/v1/statement-formats/{primary.id}/hide",
    )
    assert_response_matches_openapi(
        hide_response,
        "hideFormat",
        httpx.codes.NO_CONTENT,
        openapi_snapshot,
    )
    assert_no_content(hide_response)
    _require_format_visibility(
        _list_formats(
            gateway_client,
            include_hidden=False,
            openapi_snapshot=openapi_snapshot,
        ),
        absent=(primary.id,),
    )
    owner_hidden = _find_format(
        _list_formats(
            gateway_client,
            include_hidden=True,
            openapi_snapshot=openapi_snapshot,
        ),
        primary.id,
    )
    if not bool_field(owner_hidden, "hidden"):
        raise AssertionError("the owner's hidden format did not expose its preference state")

    admin_after_owner_hide = _find_format(
        _list_formats(
            admin_gateway_client,
            include_hidden=False,
            openapi_snapshot=openapi_snapshot,
        ),
        primary.id,
    )
    if bool_field(admin_after_owner_hide, "hidden"):
        raise AssertionError("the owner's hidden state changed the admin caller's preference")
    _assert_not_found(
        admin_gateway_client.api_request(
            "POST",
            f"/v1/statement-formats/{primary.id}/unhide",
        ),
        "unhideFormat",
        openapi_snapshot,
    )

    unhide_response = gateway_client.api_request(
        "POST",
        f"/v1/statement-formats/{primary.id}/unhide",
    )
    assert_response_matches_openapi(
        unhide_response,
        "unhideFormat",
        httpx.codes.NO_CONTENT,
        openapi_snapshot,
    )
    assert_no_content(unhide_response)
    owner_restored = _find_format(
        _list_formats(
            gateway_client,
            include_hidden=False,
            openapi_snapshot=openapi_snapshot,
        ),
        primary.id,
    )
    if bool_field(owner_restored, "hidden"):
        raise AssertionError("the owner's unhide did not restore normal list visibility")

    for created, owner_client, label, currency, enabled in (
        (primary, gateway_client, "admin-primary-format", "EUR", False),
        (secondary, secondary_gateway_client, "admin-secondary-format", "GBP", True),
    ):
        update = update_statement_format_request(run_state, label)
        update["defaultCurrencyIsoCode"] = currency
        update["enabled"] = enabled
        response = admin_gateway_client.api_request(
            "PUT",
            f"/v1/statement-formats/{created.id}",
            json=update,
        )
        assert_response_matches_openapi(
            response,
            "updateFormat",
            httpx.codes.OK,
            openapi_snapshot,
        )
        updated = json_object_response(response, httpx.codes.OK)
        _assert_format_invariants(updated, persisted_baselines[created.id])
        expected_updates = (
            ("displayName", update["displayName"]),
            ("bankName", update["bankName"]),
            ("defaultCurrencyIsoCode", update["defaultCurrencyIsoCode"]),
            ("enabled", update["enabled"]),
        )
        for field, expected in expected_updates:
            if updated.get(field) != expected:
                raise AssertionError(f"admin format update did not persist {field}")
        string_field(updated, "updatedAt")
        string_field(updated, "updatedBy")

        owner_visible = _get_format(owner_client, created.id, openapi_snapshot)
        for field, expected in expected_updates:
            if owner_visible.get(field) != expected:
                raise AssertionError(f"format owner did not observe updated {field}")

    before_invalid = _get_format(admin_gateway_client, primary.id, openapi_snapshot)
    invalid_response = admin_gateway_client.api_request(
        "PUT",
        f"/v1/statement-formats/{primary.id}",
        json={"displayName": "x" * 101, "defaultCurrencyIsoCode": "US"},
    )
    # The shared ApiErrorResponse snapshot models rejectedValue as an object,
    # while Spring validation returns each rejected scalar. Keep the stable
    # public error fields covered without masking that contract mismatch.
    invalid_body = assert_api_error(
        invalid_response,
        httpx.codes.BAD_REQUEST,
        error_type="VALIDATION_ERROR",
    )
    invalid_fields = {
        string_field(object_item(item), "field")
        for item in array_field(invalid_body, "fieldErrors")
    }
    if invalid_fields != {"defaultCurrencyIsoCode", "displayName"}:
        raise AssertionError("invalid admin format update identified unexpected fields")
    after_invalid = _get_format(admin_gateway_client, primary.id, openapi_snapshot)
    for field in (
        "id",
        "displayName",
        "bankName",
        "defaultCurrencyIsoCode",
        "enabled",
        "ownerId",
        "scope",
        "formatType",
    ):
        if after_invalid.get(field) != before_invalid.get(field):
            raise AssertionError(f"invalid admin update changed format field {field}")


def _assert_created_user_format(created: CreatedStatementFormat) -> None:
    body = created.body
    if body.get("scope") != "USER" or body.get("formatType") != "CSV":
        raise AssertionError("test setup did not create a USER-scoped CSV format")
    string_field(body, "ownerId")
    string_field(body, "createdAt")
    string_field(body, "createdBy")


def _assert_format_invariants(actual: JsonObject, original: JsonObject) -> None:
    for field in ("id", "ownerId", "scope", "formatType", "createdBy"):
        if actual.get(field) != original.get(field):
            raise AssertionError(f"admin update changed immutable format field {field}")
    actual_created_at = _timestamp(string_field(actual, "createdAt"))
    original_created_at = _timestamp(string_field(original, "createdAt"))
    if abs(actual_created_at - original_created_at) > timedelta(microseconds=1):
        raise AssertionError("admin update changed immutable format field createdAt")


def _list_formats(
    client: GatewayClient,
    include_hidden: bool,
    openapi_snapshot: OpenApiDocument,
) -> tuple[JsonObject, ...]:
    response = client.api_request(
        "GET",
        "/v1/statement-formats",
        params={"includeHidden": include_hidden},
    )
    assert_response_matches_openapi(response, "listFormats", httpx.codes.OK, openapi_snapshot)
    return tuple(object_item(item) for item in json_array_response(response, httpx.codes.OK))


def _get_format(
    client: GatewayClient,
    statement_format_id: int,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    response = client.api_request("GET", f"/v1/statement-formats/{statement_format_id}")
    assert_response_matches_openapi(response, "getFormat", httpx.codes.OK, openapi_snapshot)
    return json_object_response(response, httpx.codes.OK)


def _find_format(formats: Sequence[JsonValue], statement_format_id: int) -> JsonObject:
    for item in formats:
        statement_format = object_item(item)
        if statement_format.get("id") == statement_format_id:
            return statement_format
    raise AssertionError("an expected test-created statement format was absent")


def _require_format_visibility(
    formats: Sequence[JsonValue],
    *,
    present: Sequence[int] = (),
    absent: Sequence[int] = (),
) -> None:
    ids = {int_field(object_item(item), "id") for item in formats}
    if not set(present).issubset(ids):
        raise AssertionError("a visible test-created statement format was absent")
    if set(absent) & ids:
        raise AssertionError("a foreign or hidden test-created statement format was visible")


def _assert_not_found(
    response: httpx.Response,
    operation_id: str,
    openapi_snapshot: OpenApiDocument,
) -> None:
    assert_response_matches_openapi(
        response,
        operation_id,
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    assert_api_error(response, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
