from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import httpx
import pytest

from api_tests.assertions import (
    JsonObject,
    array_field,
    assert_api_error,
    bool_field,
    int_field,
    json_object_response,
    object_field,
    object_item,
    string_array_field,
    string_field,
)
from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi
from api_tests.session_gateway_assertions import AuthenticatedIdentity

type QueryValue = str | int | list[str]
type QueryParams = dict[str, QueryValue]

_SORT_FIELDS = (
    "id",
    "email",
    "displayName",
    "status",
    "createdAt",
    "updatedAt",
    "deactivatedAt",
)


@dataclass(frozen=True, slots=True, repr=False)
class _UserRecord:
    id: str
    idp_sub: str
    email: str
    display_name: str
    status: str
    role_ids: frozenset[str]
    created_at: str
    updated_at: str | None
    deactivated_at: str | None


@dataclass(frozen=True, slots=True, repr=False)
class _UserPage:
    content: tuple[_UserRecord, ...]
    page: int
    size: int
    number_of_elements: int
    total_elements: int
    total_pages: int
    first: bool
    last: bool


@dataclass(frozen=True, slots=True, repr=False)
class _AdminUserDataset:
    primary: _UserRecord
    secondary: _UserRecord
    admin: _UserRecord

    @property
    def all(self) -> tuple[_UserRecord, ...]:
        return (self.primary, self.secondary, self.admin)


@pytest.fixture(scope="module")
def admin_user_dataset(
    admin_gateway_client: GatewayClient,
    distinct_admin_user_identities: None,
    primary_identity: AuthenticatedIdentity,
    secondary_identity: AuthenticatedIdentity,
    admin_identity: AuthenticatedIdentity,
    openapi_snapshot: OpenApiDocument,
) -> _AdminUserDataset:
    assert distinct_admin_user_identities is None
    records = tuple(
        _resolve_identity(admin_gateway_client, identity, openapi_snapshot)
        for identity in (primary_identity, secondary_identity, admin_identity)
    )
    if len({record.id for record in records}) != 3:
        raise AssertionError("the three public subjects did not resolve to distinct internal users")
    return _AdminUserDataset(*records)


@pytest.mark.openapi("getUsers")
@pytest.mark.admin
@pytest.mark.readonly
def test_admin_user_search_covers_filters_text_matching_and_combinations(
    admin_gateway_client: GatewayClient,
    admin_user_dataset: _AdminUserDataset,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    target = admin_user_dataset.primary
    exact_filters: tuple[QueryParams, ...] = (
        {"id": target.id},
        {"idpSub": target.idp_sub},
        {"idpSub": target.idp_sub, "status": "ACTIVE"},
        {"idpSub": target.idp_sub, "createdAfter": target.created_at},
        {"idpSub": target.idp_sub, "createdBefore": target.created_at},
        {
            "idpSub": target.idp_sub,
            "updatedAfter": _required_timestamp(target.updated_at, "updatedAt"),
        },
        {
            "idpSub": target.idp_sub,
            "updatedBefore": _required_timestamp(target.updated_at, "updatedAt"),
        },
        {
            "id": target.id,
            "email": target.email,
            "displayName": target.display_name,
            "idpSub": target.idp_sub,
            "status": "ACTIVE",
            "createdAfter": target.created_at,
            "createdBefore": target.created_at,
            "updatedAfter": _required_timestamp(target.updated_at, "updatedAt"),
            "updatedBefore": _required_timestamp(target.updated_at, "updatedAt"),
        },
    )
    for query in exact_filters:
        page = _search_users(admin_gateway_client, openapi_snapshot, query)
        _require_only_user(page, target.id)

    text_filters: tuple[QueryParams, ...] = (
        {"email": target.email.swapcase()},
        {"displayName": target.display_name.swapcase()},
    )
    for query in text_filters:
        page = _search_users(admin_gateway_client, openapi_snapshot, query)
        _require_users_present(page, (target.id,))

    email_or_page = _search_users(
        admin_gateway_client,
        openapi_snapshot,
        {"email": f"{admin_user_dataset.primary.email} {admin_user_dataset.secondary.email}"},
    )
    _require_users_present(
        email_or_page,
        (admin_user_dataset.primary.id, admin_user_dataset.secondary.id),
    )

    display_name_terms = " ".join(
        record.display_name.split()[0]
        for record in (admin_user_dataset.primary, admin_user_dataset.secondary)
    )
    display_name_or_page = _search_users(
        admin_gateway_client,
        openapi_snapshot,
        {"displayName": display_name_terms},
    )
    _require_users_present(
        display_name_or_page,
        (admin_user_dataset.primary.id, admin_user_dataset.secondary.id),
    )

    wildcard_page = _search_users(
        admin_gateway_client,
        openapi_snapshot,
        {"email": f"%_{run_state.run_id}_literal"},
    )
    _require_users_absent(wildcard_page, (record.id for record in admin_user_dataset.all))

    missing_page = _search_users(
        admin_gateway_client,
        openapi_snapshot,
        {"id": f"usr_{run_state.run_id}_missing"},
    )
    if missing_page.content or missing_page.total_elements != 0:
        raise AssertionError("an exact missing-user filter unexpectedly returned a row")


@pytest.mark.openapi("getUsers")
@pytest.mark.admin
@pytest.mark.readonly
def test_admin_user_search_supports_sorts_stable_ties_and_exact_paging(
    admin_gateway_client: GatewayClient,
    admin_user_dataset: _AdminUserDataset,
    openapi_snapshot: OpenApiDocument,
) -> None:
    for field in _SORT_FIELDS:
        for direction in ("asc", "desc"):
            page = _search_users(
                admin_gateway_client,
                openapi_snapshot,
                {
                    "status": "ACTIVE",
                    "size": 100,
                    "sort": [f"{field},{direction}", f"id,{direction}"],
                },
            )
            _assert_sorted(page.content, field, direction)

    first_page = _search_users(
        admin_gateway_client,
        openapi_snapshot,
        {
            "idpSub": admin_user_dataset.admin.idp_sub,
            "page": 0,
            "size": 1,
            "sort": ["id,asc"],
        },
    )
    _require_only_user(first_page, admin_user_dataset.admin.id)
    if (
        first_page.page != 0
        or first_page.size != 1
        or first_page.number_of_elements != 1
        or first_page.total_elements != 1
        or first_page.total_pages != 1
        or not first_page.first
        or not first_page.last
    ):
        raise AssertionError("exact user paging metadata did not describe its one-row result")

    empty_page = _search_users(
        admin_gateway_client,
        openapi_snapshot,
        {"idpSub": admin_user_dataset.admin.idp_sub, "page": 1, "size": 1},
    )
    if (
        empty_page.content
        or empty_page.page != 1
        or empty_page.number_of_elements != 0
        or empty_page.total_elements != 1
        or empty_page.total_pages != 1
        or empty_page.first
        or not empty_page.last
    ):
        raise AssertionError("exact user empty-page metadata was inconsistent")


@pytest.mark.openapi("getUsers")
@pytest.mark.admin
@pytest.mark.readonly
def test_admin_user_search_rejects_invalid_sort_and_filter_bindings(
    admin_gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    unsupported_sort = admin_gateway_client.api_request(
        "GET",
        "/v1/users",
        params={"sort": "unsupported,asc"},
    )
    assert_response_matches_openapi(
        unsupported_sort,
        "getUsers",
        httpx.codes.BAD_REQUEST,
        openapi_snapshot,
    )
    assert_api_error(
        unsupported_sort,
        httpx.codes.BAD_REQUEST,
        error_type="INVALID_REQUEST",
    )

    for query, expected_field in (
        ({"status": "UNKNOWN"}, "status"),
        ({"createdAfter": "not-a-timestamp"}, "createdAfter"),
    ):
        response = admin_gateway_client.api_request("GET", "/v1/users", params=query)
        body = assert_api_error(
            response,
            httpx.codes.BAD_REQUEST,
            error_type="VALIDATION_ERROR",
        )
        field_errors = array_field(body, "fieldErrors")
        if len(field_errors) != 1:
            raise AssertionError("invalid user filter did not return one field error")
        if string_field(object_item(field_errors[0]), "field") != expected_field:
            raise AssertionError("invalid user filter identified the wrong field")


@pytest.mark.openapi("getUser")
@pytest.mark.admin
@pytest.mark.readonly
def test_admin_reads_each_user_detail_and_schema_backed_missing_user(
    admin_gateway_client: GatewayClient,
    admin_user_dataset: _AdminUserDataset,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    for listed in admin_user_dataset.all:
        response = admin_gateway_client.api_request("GET", f"/v1/users/{listed.id}")
        assert_response_matches_openapi(response, "getUser", httpx.codes.OK, openapi_snapshot)
        detail_body = json_object_response(response, httpx.codes.OK)
        detail = _user_record(detail_body)
        _assert_same_user(detail, listed)
        if any(
            detail_body.get(field) is not None
            for field in ("deactivatedAt", "deactivatedBy", "deletedAt", "deletedBy")
        ):
            raise AssertionError(
                "an active reusable identity unexpectedly has inactive audit state"
            )

    missing_response = admin_gateway_client.api_request(
        "GET",
        f"/v1/users/usr_{run_state.run_id}_missing",
    )
    assert_response_matches_openapi(
        missing_response,
        "getUser",
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    assert_api_error(missing_response, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")


def _resolve_identity(
    client: GatewayClient,
    identity: AuthenticatedIdentity,
    openapi_snapshot: OpenApiDocument,
) -> _UserRecord:
    page = _search_users(client, openapi_snapshot, {"idpSub": identity.subject})
    if len(page.content) != 1 or page.total_elements != 1:
        raise AssertionError("an exact public subject did not resolve to one canonical user")
    record = page.content[0]
    _safe_equal(record.idp_sub, identity.subject, "public subject")
    if identity.email is None or identity.display_name is None:
        raise AssertionError("the authenticated identity omitted required profile fields")
    _safe_equal(record.email, identity.email, "email profile")
    _safe_equal(record.display_name, identity.display_name, "display-name profile")
    if record.role_ids != identity.roles:
        raise AssertionError("permission-service roles differ from the authenticated session roles")
    if record.status != "ACTIVE" or record.deactivated_at is not None:
        raise AssertionError("a reusable test identity is not active")
    _timestamp(record.created_at)
    _timestamp(_required_timestamp(record.updated_at, "updatedAt"))
    return record


def _search_users(
    client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
    query: QueryParams,
) -> _UserPage:
    response = client.api_request("GET", "/v1/users", params=query)
    assert_response_matches_openapi(response, "getUsers", httpx.codes.OK, openapi_snapshot)
    body = json_object_response(response, httpx.codes.OK)
    metadata = object_field(body, "metadata")
    return _UserPage(
        content=tuple(_user_record(object_item(item)) for item in array_field(body, "content")),
        page=int_field(metadata, "page"),
        size=int_field(metadata, "size"),
        number_of_elements=int_field(metadata, "numberOfElements"),
        total_elements=int_field(metadata, "totalElements"),
        total_pages=int_field(metadata, "totalPages"),
        first=bool_field(metadata, "first"),
        last=bool_field(metadata, "last"),
    )


def _user_record(body: JsonObject) -> _UserRecord:
    deactivated_at = body.get("deactivatedAt")
    if deactivated_at is not None and not isinstance(deactivated_at, str):
        raise AssertionError("user deactivation timestamp had an invalid shape")
    return _UserRecord(
        id=string_field(body, "id"),
        idp_sub=string_field(body, "idpSub"),
        email=string_field(body, "email"),
        display_name=string_field(body, "displayName"),
        status=string_field(body, "status"),
        role_ids=frozenset(string_array_field(body, "roleIds")),
        created_at=string_field(body, "createdAt"),
        updated_at=_optional_string_field(body, "updatedAt"),
        deactivated_at=deactivated_at,
    )


def _require_only_user(page: _UserPage, expected_id: str) -> None:
    if len(page.content) != 1 or page.total_elements != 1:
        raise AssertionError("an exact user query did not return exactly one row")
    _safe_equal(page.content[0].id, expected_id, "canonical user id")


def _require_users_present(page: _UserPage, expected_ids: Iterable[str]) -> None:
    expected = set(expected_ids)
    actual = {record.id for record in page.content}
    if not expected.issubset(actual):
        raise AssertionError("a user query omitted an expected reusable identity")


def _require_users_absent(page: _UserPage, unexpected_ids: Iterable[str]) -> None:
    unexpected = set(unexpected_ids)
    actual = {record.id for record in page.content}
    if unexpected & actual:
        raise AssertionError("an escaped text filter unexpectedly matched a reusable identity")


def _assert_same_user(actual: _UserRecord, expected: _UserRecord) -> None:
    for label, actual_value, expected_value in (
        ("id", actual.id, expected.id),
        ("subject", actual.idp_sub, expected.idp_sub),
        ("email", actual.email, expected.email),
        ("display name", actual.display_name, expected.display_name),
        ("status", actual.status, expected.status),
        ("roles", actual.role_ids, expected.role_ids),
        ("created timestamp", actual.created_at, expected.created_at),
        ("updated timestamp", actual.updated_at, expected.updated_at),
        ("deactivation timestamp", actual.deactivated_at, expected.deactivated_at),
    ):
        _safe_equal(actual_value, expected_value, label)


def _assert_sorted(records: tuple[_UserRecord, ...], field: str, direction: str) -> None:
    reverse = direction == "desc"
    values = [_sort_value(record, field) for record in records]
    non_null_values = [value for value in values if value is not None]
    if non_null_values != sorted(non_null_values, reverse=reverse):
        raise AssertionError(f"user results were not sorted by {field} {direction}")
    for left, right, left_value, right_value in zip(
        records,
        records[1:],
        values,
        values[1:],
        strict=False,
    ):
        if left_value == right_value and ((left.id > right.id) is not reverse):
            raise AssertionError(
                f"user results did not use the requested id tie-breaker for {field}"
            )


def _sort_value(record: _UserRecord, field: str) -> str | None:
    values = {
        "id": record.id,
        "email": record.email,
        "displayName": record.display_name,
        "status": record.status,
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
        "deactivatedAt": record.deactivated_at,
    }
    return values[field]


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _optional_string_field(body: JsonObject, field: str) -> str | None:
    value = body.get(field)
    if value is not None and not isinstance(value, str):
        raise AssertionError(f"optional field {field} had an invalid shape")
    return value


def _required_timestamp(value: str | None, field: str) -> str:
    if value is None:
        raise AssertionError(f"reusable identity omitted required timestamp {field}")
    return value


def _safe_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"sensitive {label} values did not match")
