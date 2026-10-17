from __future__ import annotations

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
    json_scalar_response,
    object_field,
    object_item,
    string_field,
)
from api_tests.builders.transactions import TransactionType
from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument
from api_tests.resources import CreatedTransaction, create_manual_transaction
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi

type QueryValue = str | int | float | list[str]
type QueryParams = dict[str, QueryValue]

_SORT_FIELDS = (
    "id",
    "ownerId",
    "accountId",
    "bankName",
    "date",
    "currencyIsoCode",
    "amount",
    "type",
    "description",
    "createdAt",
    "updatedAt",
)


@dataclass(frozen=True, slots=True)
class _TransactionSpec:
    label: str
    account_id: str
    bank_name: str
    description: str
    amount: float
    currency_iso_code: str
    transaction_type: TransactionType
    date: str


@dataclass(frozen=True, slots=True)
class _AdminTransactionDataset:
    discriminator: str
    primary: tuple[CreatedTransaction, ...]
    secondary: tuple[CreatedTransaction, ...]

    @property
    def all(self) -> tuple[CreatedTransaction, ...]:
        return self.primary + self.secondary

    @property
    def all_ids(self) -> frozenset[int]:
        return frozenset(transaction.id for transaction in self.all)


@dataclass(frozen=True, slots=True)
class _SearchPage:
    content: tuple[JsonObject, ...]
    page: int
    size: int
    number_of_elements: int
    total_elements: int
    total_pages: int
    first: bool
    last: bool


@pytest.fixture(scope="module")
def admin_transaction_dataset(
    gateway_client: GatewayClient,
    secondary_gateway_client: GatewayClient,
    distinct_admin_user_identities: None,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> _AdminTransactionDataset:
    assert distinct_admin_user_identities is None
    discriminator = f"{run_state.run_id}-adminsearch"
    shared_account = f"{discriminator}-shared-account"
    primary_specs = (
        _TransactionSpec(
            label="admin-search-primary-alpha",
            account_id=shared_account,
            bank_name=f"North-{discriminator}",
            description=f"{discriminator} {discriminator}-Alpha {discriminator}%_",
            amount=10.01,
            currency_iso_code="USD",
            transaction_type="DEBIT",
            date="2026-02-10",
        ),
        _TransactionSpec(
            label="admin-search-primary-beta",
            account_id=f"{discriminator}-primary-beta-account",
            bank_name=f"South-{discriminator}",
            description=f"{discriminator} {discriminator}-Beta",
            amount=20.02,
            currency_iso_code="EUR",
            transaction_type="CREDIT",
            date="2026-02-10",
        ),
        _TransactionSpec(
            label="admin-search-primary-gamma",
            account_id=f"{discriminator}-primary-gamma-account",
            bank_name=f"North-{discriminator}",
            description=f"{discriminator} {discriminator}-Gamma",
            amount=30.03,
            currency_iso_code="USD",
            transaction_type="DEBIT",
            date="2026-02-11",
        ),
    )
    secondary_specs = (
        _TransactionSpec(
            label="admin-search-secondary-delta",
            account_id=shared_account,
            bank_name=f"East-{discriminator}",
            description=f"{discriminator} {discriminator}-Delta",
            amount=20.02,
            currency_iso_code="USD",
            transaction_type="CREDIT",
            date="2026-02-10",
        ),
        _TransactionSpec(
            label="admin-search-secondary-epsilon",
            account_id=f"{discriminator}-secondary-epsilon-account",
            bank_name=f"South-{discriminator}",
            description=f"{discriminator} {discriminator}-Epsilon",
            amount=10.01,
            currency_iso_code="EUR",
            transaction_type="DEBIT",
            date="2026-02-11",
        ),
        _TransactionSpec(
            label="admin-search-secondary-zeta",
            account_id=f"{discriminator}-secondary-zeta-account",
            bank_name=f"East-{discriminator}",
            description=f"{discriminator} {discriminator}-Zeta",
            amount=40.04,
            currency_iso_code="USD",
            transaction_type="CREDIT",
            date="2026-02-12",
        ),
    )
    return _AdminTransactionDataset(
        discriminator=discriminator,
        primary=_create_transactions(gateway_client, run_state, primary_specs, openapi_snapshot),
        secondary=_create_transactions(
            secondary_gateway_client, run_state, secondary_specs, openapi_snapshot
        ),
    )


@pytest.mark.openapi("searchTransactions")
@pytest.mark.openapi("countTransactionsAcrossUsers")
@pytest.mark.admin
@pytest.mark.mutation
def test_admin_search_and_count_cover_all_filters_with_exact_cross_user_results(
    gateway_client: GatewayClient,
    secondary_gateway_client: GatewayClient,
    admin_gateway_client: GatewayClient,
    admin_transaction_dataset: _AdminTransactionDataset,
    openapi_snapshot: OpenApiDocument,
) -> None:
    dataset = admin_transaction_dataset
    primary_owner = _one_owner(dataset.primary)
    secondary_owner = _one_owner(dataset.secondary)
    assert primary_owner != secondary_owner

    for client, records in (
        (gateway_client, dataset.primary),
        (secondary_gateway_client, dataset.secondary),
    ):
        for record in records:
            own_response = client.api_request("GET", f"/v1/transactions/{record.id}")
            assert_response_matches_openapi(
                own_response, "getTransaction", httpx.codes.OK, openapi_snapshot
            )
            _assert_same_transaction(
                json_object_response(own_response, httpx.codes.OK), record.body
            )

    created_times = [_timestamp(record.body, "createdAt") for record in dataset.all]
    updated_times = [_timestamp(record.body, "updatedAt") for record in dataset.all]
    filters: tuple[tuple[QueryParams, set[int]], ...] = (
        ({"id": dataset.primary[0].id}, {dataset.primary[0].id}),
        (
            {"description": dataset.discriminator, "ownerId": primary_owner},
            {record.id for record in dataset.primary},
        ),
        (
            {
                "description": dataset.discriminator,
                "accountId": f"{dataset.discriminator}-shared-account",
            },
            {dataset.primary[0].id, dataset.secondary[0].id},
        ),
        (
            {
                "description": dataset.discriminator,
                "bankName": f"NORTH-{dataset.discriminator.upper()}",
            },
            {dataset.primary[0].id, dataset.primary[2].id},
        ),
        (
            {"description": dataset.discriminator, "dateFrom": "2026-02-11"},
            {dataset.primary[2].id, dataset.secondary[1].id, dataset.secondary[2].id},
        ),
        (
            {"description": dataset.discriminator, "dateTo": "2026-02-10"},
            {dataset.primary[0].id, dataset.primary[1].id, dataset.secondary[0].id},
        ),
        (
            {"description": dataset.discriminator, "currencyIsoCode": "usd"},
            {
                dataset.primary[0].id,
                dataset.primary[2].id,
                dataset.secondary[0].id,
                dataset.secondary[2].id,
            },
        ),
        (
            {"description": dataset.discriminator, "minAmount": 20.02, "maxAmount": 20.02},
            {dataset.primary[1].id, dataset.secondary[0].id},
        ),
        (
            {
                "description": dataset.discriminator,
                "currencyIsoCode": "EUR",
                "minAmount": 10.01,
                "maxAmount": 20.02,
            },
            {dataset.primary[1].id, dataset.secondary[1].id},
        ),
        (
            {"description": dataset.discriminator, "type": "DEBIT"},
            {dataset.primary[0].id, dataset.primary[2].id, dataset.secondary[1].id},
        ),
        (
            {"description": f"{dataset.discriminator}-ALPHA"},
            {dataset.primary[0].id},
        ),
        (
            {"description": (f"{dataset.discriminator}-Beta {dataset.discriminator}-ZETA")},
            {dataset.primary[1].id, dataset.secondary[2].id},
        ),
        (
            {"description": f"{dataset.discriminator}%_"},
            {dataset.primary[0].id},
        ),
        (
            {
                "description": dataset.discriminator,
                "createdAfter": string_field(dataset.primary[2].body, "createdAt"),
            },
            {
                record.id
                for record, timestamp in zip(dataset.all, created_times, strict=True)
                if timestamp >= created_times[2]
            },
        ),
        (
            {
                "description": dataset.discriminator,
                "createdBefore": string_field(dataset.secondary[0].body, "createdAt"),
            },
            {
                record.id
                for record, timestamp in zip(dataset.all, created_times, strict=True)
                if timestamp <= created_times[3]
            },
        ),
        (
            {
                "description": dataset.discriminator,
                "updatedAfter": string_field(dataset.secondary[0].body, "updatedAt"),
            },
            {
                record.id
                for record, timestamp in zip(dataset.all, updated_times, strict=True)
                if timestamp >= updated_times[3]
            },
        ),
        (
            {
                "description": dataset.discriminator,
                "updatedBefore": string_field(dataset.primary[2].body, "updatedAt"),
            },
            {
                record.id
                for record, timestamp in zip(dataset.all, updated_times, strict=True)
                if timestamp <= updated_times[2]
            },
        ),
        (
            {
                "description": dataset.discriminator,
                "ownerId": secondary_owner,
                "dateFrom": "2026-02-10",
                "dateTo": "2026-02-12",
                "currencyIsoCode": "USD",
                "minAmount": 20.02,
                "maxAmount": 40.04,
                "type": "CREDIT",
            },
            {dataset.secondary[0].id, dataset.secondary[2].id},
        ),
        (
            {
                "description": dataset.discriminator,
                "accountId": f"{dataset.discriminator}-missing-account",
            },
            set(),
        ),
    )

    for query, expected_ids in filters:
        page = _search(admin_gateway_client, openapi_snapshot, query)
        assert _ids(page.content) == expected_ids
        assert page.total_elements == len(expected_ids)
        assert page.number_of_elements == len(expected_ids)
        assert _count(admin_gateway_client, openapi_snapshot, query) == len(expected_ids)


@pytest.mark.openapi("searchTransactions")
@pytest.mark.admin
@pytest.mark.mutation
def test_admin_search_supports_every_sort_default_order_and_compound_ties(
    admin_gateway_client: GatewayClient,
    admin_transaction_dataset: _AdminTransactionDataset,
    openapi_snapshot: OpenApiDocument,
) -> None:
    dataset = admin_transaction_dataset
    base_query: QueryParams = {"description": dataset.discriminator, "size": 100}

    for field in _SORT_FIELDS:
        for direction in ("asc", "desc"):
            sort = [f"{field},{direction}"]
            if field != "id":
                sort.append(f"id,{direction}")
            page = _search(
                admin_gateway_client,
                openapi_snapshot,
                {**base_query, "sort": sort},
            )
            expected = sorted(
                dataset.all,
                key=lambda record: (_sort_value(record.body, field), record.id),
                reverse=direction == "desc",
            )
            assert [int_field(item, "id") for item in page.content] == [
                record.id for record in expected
            ]

    default_page = _search(admin_gateway_client, openapi_snapshot, base_query)
    default_expected = sorted(
        dataset.all,
        key=lambda record: (string_field(record.body, "date"), record.id),
        reverse=True,
    )
    assert [int_field(item, "id") for item in default_page.content] == [
        record.id for record in default_expected
    ]

    compound_page = _search(
        admin_gateway_client,
        openapi_snapshot,
        {
            **base_query,
            "sort": ["date,asc", "amount,desc", "id,asc"],
        },
    )
    compound_expected = sorted(dataset.all, key=lambda record: record.id)
    compound_expected.sort(key=lambda record: _amount(record.body), reverse=True)
    compound_expected.sort(key=lambda record: string_field(record.body, "date"))
    assert [int_field(item, "id") for item in compound_page.content] == [
        record.id for record in compound_expected
    ]


@pytest.mark.openapi("searchTransactions")
@pytest.mark.openapi("countTransactionsAcrossUsers")
@pytest.mark.admin
@pytest.mark.mutation
def test_admin_search_pagination_is_complete_and_count_ignores_page_size(
    admin_gateway_client: GatewayClient,
    admin_transaction_dataset: _AdminTransactionDataset,
    openapi_snapshot: OpenApiDocument,
) -> None:
    dataset = admin_transaction_dataset
    traversed_ids: list[int] = []
    for page_number in range(3):
        page = _search(
            admin_gateway_client,
            openapi_snapshot,
            {
                "description": dataset.discriminator,
                "page": page_number,
                "size": 2,
                "sort": ["id,asc"],
            },
        )
        assert page.page == page_number
        assert page.size == 2
        assert page.number_of_elements == 2
        assert page.total_elements == len(dataset.all)
        assert page.total_pages == 3
        assert page.first is (page_number == 0)
        assert page.last is (page_number == 2)
        traversed_ids.extend(int_field(item, "id") for item in page.content)

    assert traversed_ids == sorted(dataset.all_ids)
    assert len(traversed_ids) == len(set(traversed_ids))

    empty_page = _search(
        admin_gateway_client,
        openapi_snapshot,
        {
            "description": dataset.discriminator,
            "page": 3,
            "size": 2,
            "sort": ["id,asc"],
        },
    )
    assert empty_page.content == ()
    assert empty_page.number_of_elements == 0
    assert empty_page.total_elements == len(dataset.all)
    assert empty_page.total_pages == 3
    assert empty_page.first is False
    assert empty_page.last is True
    assert _count(
        admin_gateway_client,
        openapi_snapshot,
        {"description": dataset.discriminator},
    ) == len(dataset.all)


@pytest.mark.openapi("searchTransactions")
@pytest.mark.admin
@pytest.mark.readonly
def test_admin_search_rejects_unsupported_sort_against_response_contract(
    admin_gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = admin_gateway_client.api_request(
        "GET",
        "/v1/transactions/search",
        params={"sort": "unsupported,asc"},
    )

    assert_response_matches_openapi(
        response, "searchTransactions", httpx.codes.BAD_REQUEST, openapi_snapshot
    )
    assert_api_error(response, httpx.codes.BAD_REQUEST, error_type="INVALID_REQUEST")


@pytest.mark.openapi("searchTransactions")
@pytest.mark.admin
@pytest.mark.readonly
@pytest.mark.parametrize(
    ("query", "field"),
    (
        ({"dateFrom": "not-a-date"}, "dateFrom"),
        ({"type": "SIDEWAYS"}, "type"),
    ),
)
def test_admin_search_rejects_malformed_filter_bindings(
    admin_gateway_client: GatewayClient,
    query: QueryParams,
    field: str,
) -> None:
    response = admin_gateway_client.api_request("GET", "/v1/transactions/search", params=query)

    # The shared ApiErrorResponse snapshot currently models rejectedValue as an
    # object, while Spring query binding returns the rejected scalar. Keep the
    # stable public fields covered without hiding that documented schema gap.
    body = assert_api_error(
        response,
        httpx.codes.BAD_REQUEST,
        error_type="VALIDATION_ERROR",
    )
    field_errors = array_field(body, "fieldErrors")
    assert len(field_errors) == 1
    assert string_field(object_item(field_errors[0]), "field") == field


def _create_transactions(
    client: GatewayClient,
    run_state: RunState,
    specs: tuple[_TransactionSpec, ...],
    openapi_snapshot: OpenApiDocument,
) -> tuple[CreatedTransaction, ...]:
    created = tuple(
        create_manual_transaction(
            client,
            run_state,
            spec.label,
            account_id=spec.account_id,
            bank_name=spec.bank_name,
            description=spec.description,
            amount=spec.amount,
            currency_iso_code=spec.currency_iso_code,
            transaction_type=spec.transaction_type,
            date=spec.date,
            openapi_snapshot=openapi_snapshot,
        )
        for spec in specs
    )
    persisted: list[CreatedTransaction] = []
    for transaction in created:
        response = client.api_request("GET", f"/v1/transactions/{transaction.id}")
        assert_response_matches_openapi(
            response, "getTransaction", httpx.codes.OK, openapi_snapshot
        )
        persisted.append(
            CreatedTransaction(
                id=transaction.id,
                account_id=transaction.account_id,
                body=json_object_response(response, httpx.codes.OK),
            )
        )
    return tuple(persisted)


def _search(
    client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
    query: QueryParams,
) -> _SearchPage:
    response = client.api_request("GET", "/v1/transactions/search", params=query)
    assert_response_matches_openapi(
        response, "searchTransactions", httpx.codes.OK, openapi_snapshot
    )
    body = json_object_response(response, httpx.codes.OK)
    metadata = object_field(body, "metadata")
    return _SearchPage(
        content=tuple(object_item(item) for item in array_field(body, "content")),
        page=int_field(metadata, "page"),
        size=int_field(metadata, "size"),
        number_of_elements=int_field(metadata, "numberOfElements"),
        total_elements=int_field(metadata, "totalElements"),
        total_pages=int_field(metadata, "totalPages"),
        first=bool_field(metadata, "first"),
        last=bool_field(metadata, "last"),
    )


def _count(
    client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
    query: QueryParams,
) -> int:
    response = client.api_request("GET", "/v1/transactions/search/count", params=query)
    assert_response_matches_openapi(
        response,
        "countTransactionsAcrossUsers",
        httpx.codes.OK,
        openapi_snapshot,
    )
    value = json_scalar_response(response, httpx.codes.OK)
    if not isinstance(value, int):
        raise AssertionError(f"expected integer count response, got {value!r}")
    return value


def _one_owner(records: tuple[CreatedTransaction, ...]) -> str:
    owners = {string_field(record.body, "ownerId") for record in records}
    assert len(owners) == 1
    return owners.pop()


def _ids(records: tuple[JsonObject, ...]) -> set[int]:
    return {int_field(record, "id") for record in records}


def _timestamp(body: JsonObject, field: str) -> datetime:
    return datetime.fromisoformat(string_field(body, field).replace("Z", "+00:00"))


def _amount(body: JsonObject) -> float:
    value = body.get("amount")
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise AssertionError(f"expected numeric amount, got {value!r}")
    return float(value)


def _sort_value(body: JsonObject, field: str) -> str:
    value = body.get(field)
    if field == "id":
        if not isinstance(value, int):
            raise AssertionError(f"expected integer id, got {value!r}")
        return f"{value:020d}"
    if field == "amount":
        return f"{_amount(body):050.2f}"
    if not isinstance(value, str):
        raise AssertionError(f"expected string sort field {field!r}, got {value!r}")
    return value.casefold()


def _assert_same_transaction(actual: JsonObject, expected: JsonObject) -> None:
    for field in (
        "id",
        "ownerId",
        "accountId",
        "bankName",
        "date",
        "currencyIsoCode",
        "amount",
        "type",
        "description",
    ):
        assert actual.get(field) == expected.get(field)
    for field in ("createdAt", "updatedAt"):
        assert _timestamp(actual, field) == _timestamp(expected, field)
