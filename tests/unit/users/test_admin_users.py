from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from typing import cast

import pytest

from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument

type QueryValue = str | int | list[str]
type QueryParams = dict[str, QueryValue]
type SearchAllUsers = Callable[[GatewayClient, OpenApiDocument, QueryParams], tuple[object, ...]]
type UserAssertion = Callable[[Iterable[object], Iterable[str]], None]
type SortAssertion = Callable[[tuple[object, ...], str, str], None]
type RecordFactory = Callable[..., object]
type PageFactory = Callable[..., object]

_ADMIN_USERS_MODULE = importlib.import_module("tests.api.users.test_admin_users")
_SEARCH_ALL_USERS = cast(SearchAllUsers, _ADMIN_USERS_MODULE._search_all_users)
_REQUIRE_USERS_PRESENT = cast(UserAssertion, _ADMIN_USERS_MODULE._require_users_present)
_REQUIRE_USERS_ABSENT = cast(UserAssertion, _ADMIN_USERS_MODULE._require_users_absent)
_ASSERT_SORTED = cast(SortAssertion, _ADMIN_USERS_MODULE._assert_sorted)
_USER_RECORD = cast(RecordFactory, _ADMIN_USERS_MODULE._UserRecord)
_USER_PAGE = cast(PageFactory, _ADMIN_USERS_MODULE._UserPage)
_TIMESTAMP_FIELDS = ("createdAt", "updatedAt", "deactivatedAt")


def test_complete_user_search_includes_expected_identity_from_later_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_queries: list[QueryParams] = []
    pages = (
        _page((_record("usr-first"),), page=0, total_pages=2, last=False),
        _page((_record("usr-target"),), page=1, total_pages=2, last=True),
    )

    def search_users(
        client: GatewayClient,
        openapi_snapshot: OpenApiDocument,
        query: QueryParams,
    ) -> object:
        del client, openapi_snapshot
        requested_queries.append(query)
        page_number = query["page"]
        assert isinstance(page_number, int)
        return pages[page_number]

    monkeypatch.setattr(_ADMIN_USERS_MODULE, "_search_users", search_users)
    original_query: QueryParams = {"email": "TARGET@example.invalid", "size": 1}

    records = _SEARCH_ALL_USERS(_client(), _snapshot(), original_query)

    _REQUIRE_USERS_PRESENT(records, ("usr-target",))
    assert original_query == {"email": "TARGET@example.invalid", "size": 1}
    assert requested_queries == [
        {
            "email": "TARGET@example.invalid",
            "size": 1,
            "page": 0,
            "sort": ["id,asc"],
        },
        {
            "email": "TARGET@example.invalid",
            "size": 1,
            "page": 1,
            "sort": ["id,asc"],
        },
    ]


def test_complete_user_search_absence_check_cannot_stop_after_page_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_queries: list[QueryParams] = []
    pages = (
        _page((), page=0, total_pages=2, last=False),
        _page((_record("usr-unexpected"),), page=1, total_pages=2, last=True),
    )

    def search_users(
        client: GatewayClient,
        openapi_snapshot: OpenApiDocument,
        query: QueryParams,
    ) -> object:
        del client, openapi_snapshot
        requested_queries.append(query)
        page_number = query["page"]
        assert isinstance(page_number, int)
        return pages[page_number]

    monkeypatch.setattr(_ADMIN_USERS_MODULE, "_search_users", search_users)

    records = _SEARCH_ALL_USERS(
        _client(),
        _snapshot(),
        {"email": "%_literal", "size": 1},
    )

    with pytest.raises(AssertionError, match="unexpectedly matched"):
        _REQUIRE_USERS_ABSENT(records, ("usr-unexpected",))
    assert requested_queries == [
        {"email": "%_literal", "size": 1, "page": 0, "sort": ["id,asc"]},
        {"email": "%_literal", "size": 1, "page": 1, "sort": ["id,asc"]},
    ]


def test_complete_user_search_stops_after_one_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_queries: list[QueryParams] = []

    def search_users(
        client: GatewayClient,
        openapi_snapshot: OpenApiDocument,
        query: QueryParams,
    ) -> object:
        del client, openapi_snapshot
        requested_queries.append(query)
        return _page((), page=0, total_pages=0, last=True)

    monkeypatch.setattr(_ADMIN_USERS_MODULE, "_search_users", search_users)

    assert _SEARCH_ALL_USERS(_client(), _snapshot(), {"displayName": "missing"}) == ()
    assert requested_queries == [{"displayName": "missing", "page": 0, "sort": ["id,asc"]}]


@pytest.mark.parametrize("field", _TIMESTAMP_FIELDS)
def test_timestamp_sort_uses_chronology_across_fractional_precision(field: str) -> None:
    earlier = _record_with_timestamp(field, "2026-02-01T10:00:00.123Z", "usr-a")
    later = _record_with_timestamp(field, "2026-02-01T10:00:00.123456Z", "usr-b")

    _ASSERT_SORTED((earlier, later), field, "asc")
    _ASSERT_SORTED((later, earlier), field, "desc")


@pytest.mark.parametrize("field", _TIMESTAMP_FIELDS)
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_equal_timestamp_instants_use_requested_id_tie_breaker(
    field: str,
    direction: str,
) -> None:
    utc_value = "2026-02-01T10:00:00.123Z"
    offset_value = "2026-02-01T11:00:00.123000+01:00"
    if direction == "asc":
        correctly_ordered = (
            _record_with_timestamp(field, utc_value, "usr-a"),
            _record_with_timestamp(field, offset_value, "usr-z"),
        )
        incorrectly_ordered = (
            _record_with_timestamp(field, utc_value, "usr-z"),
            _record_with_timestamp(field, offset_value, "usr-a"),
        )
    else:
        correctly_ordered = (
            _record_with_timestamp(field, offset_value, "usr-z"),
            _record_with_timestamp(field, utc_value, "usr-a"),
        )
        incorrectly_ordered = (
            _record_with_timestamp(field, offset_value, "usr-a"),
            _record_with_timestamp(field, utc_value, "usr-z"),
        )

    _ASSERT_SORTED(correctly_ordered, field, direction)
    with pytest.raises(AssertionError, match="id tie-breaker"):
        _ASSERT_SORTED(incorrectly_ordered, field, direction)


def _record(
    record_id: str,
    *,
    created_at: str = "2026-02-01T10:00:00Z",
    updated_at: str | None = "2026-02-01T10:00:01Z",
    deactivated_at: str | None = None,
) -> object:
    return _USER_RECORD(
        id=record_id,
        idp_sub=f"idp|{record_id}",
        email=f"{record_id}@example.invalid",
        display_name=record_id,
        status="ACTIVE",
        role_ids=frozenset({"USER"}),
        created_at=created_at,
        updated_at=updated_at,
        deactivated_at=deactivated_at,
    )


def _record_with_timestamp(field: str, value: str, record_id: str) -> object:
    if field == "createdAt":
        return _record(record_id, created_at=value)
    if field == "updatedAt":
        return _record(record_id, updated_at=value)
    if field == "deactivatedAt":
        return _record(record_id, deactivated_at=value)
    raise AssertionError(f"unsupported timestamp field {field}")


def _page(
    content: tuple[object, ...],
    *,
    page: int,
    total_pages: int,
    last: bool,
) -> object:
    return _USER_PAGE(
        content=content,
        page=page,
        size=1,
        number_of_elements=len(content),
        total_elements=total_pages,
        total_pages=total_pages,
        first=page == 0,
        last=last,
    )


def _client() -> GatewayClient:
    return cast(GatewayClient, object())


def _snapshot() -> OpenApiDocument:
    return cast(OpenApiDocument, object())
