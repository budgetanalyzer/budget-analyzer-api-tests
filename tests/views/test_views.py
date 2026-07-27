from __future__ import annotations

from collections.abc import Sequence

import httpx
import pytest

from api_tests.assertions import (
    JsonObject,
    array_field,
    assert_no_content,
    int_field,
    json_array_response,
    json_object_response,
    object_item,
    string_field,
)
from api_tests.builders.transactions import generated_account_id
from api_tests.builders.views import (
    bulk_view_transaction_request,
    create_saved_view_request,
    update_saved_view_request,
)
from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument
from api_tests.resources import (
    CreatedSavedView,
    CreatedTransaction,
    create_saved_view,
    import_generated_transaction,
)
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi


@pytest.mark.openapi("listViews")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_list_views_returns_saved_views(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request("GET", "/v1/views")

    assert_response_matches_openapi(response, "listViews", httpx.codes.OK, openapi_snapshot)
    views = json_array_response(response, httpx.codes.OK)
    for item in views:
        saved_view = object_item(item)
        string_field(saved_view, "id")
        string_field(saved_view, "name")


@pytest.mark.openapi("createView")
@pytest.mark.mutation
def test_create_view_creates_saved_view(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    payload = create_saved_view_request(run_state, "create-view", search_text=run_state.run_id)

    response = gateway_client.api_request("POST", "/v1/views", json=payload)

    assert_response_matches_openapi(response, "createView", httpx.codes.CREATED, openapi_snapshot)
    saved_view = json_object_response(response, httpx.codes.CREATED)
    assert saved_view["name"] == payload["name"]
    string_field(saved_view, "id")


@pytest.mark.openapi("getView")
@pytest.mark.mutation
def test_get_view_returns_created_saved_view(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = create_saved_view(
        gateway_client,
        run_state,
        "get-view",
        openapi_snapshot=openapi_snapshot,
    )

    response = gateway_client.api_request("GET", f"/v1/views/{created.id}")

    assert_response_matches_openapi(response, "getView", httpx.codes.OK, openapi_snapshot)
    saved_view = json_object_response(response, httpx.codes.OK)
    assert string_field(saved_view, "id") == created.id
    assert saved_view["name"] == created.body["name"]


@pytest.mark.openapi("updateView")
@pytest.mark.mutation
def test_update_view_updates_created_saved_view(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = create_saved_view(
        gateway_client,
        run_state,
        "update-view",
        openapi_snapshot=openapi_snapshot,
    )
    payload = update_saved_view_request(run_state, "update-view")

    response = gateway_client.api_request("PUT", f"/v1/views/{created.id}", json=payload)

    assert_response_matches_openapi(response, "updateView", httpx.codes.OK, openapi_snapshot)
    saved_view = json_object_response(response, httpx.codes.OK)
    assert string_field(saved_view, "id") == created.id
    assert saved_view["name"] == payload["name"]


@pytest.mark.openapi("deleteView")
@pytest.mark.destructive
@pytest.mark.mutation
def test_delete_view_removes_created_saved_view(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = create_saved_view(
        gateway_client,
        run_state,
        "delete-view",
        openapi_snapshot=openapi_snapshot,
    )

    response = gateway_client.api_request("DELETE", f"/v1/views/{created.id}")

    assert_response_matches_openapi(
        response, "deleteView", httpx.codes.NO_CONTENT, openapi_snapshot
    )
    assert_no_content(response)

    get_response = gateway_client.api_request("GET", f"/v1/views/{created.id}")
    assert_response_matches_openapi(
        get_response, "getView", httpx.codes.NOT_FOUND, openapi_snapshot
    )
    json_object_response(get_response, httpx.codes.NOT_FOUND)


@pytest.mark.openapi("getView")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_view_unknown_id_returns_not_found(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request("GET", "/v1/views/00000000-0000-0000-0000-000000000000")

    assert_response_matches_openapi(response, "getView", httpx.codes.NOT_FOUND, openapi_snapshot)
    json_object_response(response, httpx.codes.NOT_FOUND)


@pytest.mark.openapi("getViewTransactions")
@pytest.mark.mutation
def test_get_view_transactions_returns_membership_groups(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "view-transactions",
        match_transaction=True,
        openapi_snapshot=openapi_snapshot,
    )

    response = gateway_client.api_request("GET", f"/v1/views/{saved_view.id}/transactions")

    assert_response_matches_openapi(
        response,
        "getViewTransactions",
        httpx.codes.OK,
        openapi_snapshot,
    )
    membership = json_object_response(response, httpx.codes.OK)
    _assert_id_present(array_field(membership, "matched"), transaction.id, "matched")
    _assert_id_absent(array_field(membership, "pinned"), transaction.id, "pinned")
    _assert_id_absent(array_field(membership, "excluded"), transaction.id, "excluded")


@pytest.mark.openapi("pinTransaction")
@pytest.mark.mutation
def test_pin_transaction_adds_transaction_to_view(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "pin-transaction",
        match_transaction=False,
        openapi_snapshot=openapi_snapshot,
    )

    response = gateway_client.api_request(
        "POST",
        f"/v1/views/{saved_view.id}/pin/{transaction.id}",
    )

    assert_response_matches_openapi(response, "pinTransaction", httpx.codes.OK, openapi_snapshot)
    updated = json_object_response(response, httpx.codes.OK)
    assert string_field(updated, "id") == saved_view.id
    assert int_field(updated, "pinnedCount") >= 1

    membership = _fetch_view_membership(
        gateway_client,
        saved_view.id,
        openapi_snapshot,
    )
    _assert_id_present(array_field(membership, "pinned"), transaction.id, "pinned")


@pytest.mark.openapi("unpinTransaction")
@pytest.mark.mutation
def test_unpin_transaction_removes_transaction_pin(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "unpin-transaction",
        match_transaction=False,
        openapi_snapshot=openapi_snapshot,
    )
    pin_response = gateway_client.api_request(
        "POST",
        f"/v1/views/{saved_view.id}/pin/{transaction.id}",
    )
    assert_response_matches_openapi(
        pin_response, "pinTransaction", httpx.codes.OK, openapi_snapshot
    )
    json_object_response(
        pin_response,
        httpx.codes.OK,
    )

    response = gateway_client.api_request(
        "DELETE",
        f"/v1/views/{saved_view.id}/pin/{transaction.id}",
    )

    assert_response_matches_openapi(response, "unpinTransaction", httpx.codes.OK, openapi_snapshot)
    updated = json_object_response(response, httpx.codes.OK)
    assert string_field(updated, "id") == saved_view.id

    membership = _fetch_view_membership(
        gateway_client,
        saved_view.id,
        openapi_snapshot,
    )
    _assert_id_absent(array_field(membership, "pinned"), transaction.id, "pinned")


@pytest.mark.openapi("bulkPinTransactions")
@pytest.mark.mutation
def test_bulk_pin_transactions_adds_transactions_to_view(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    saved_view, transactions = _view_with_imported_transactions(
        gateway_client,
        run_state,
        "bulk-pin-transactions",
        match_transaction=False,
        openapi_snapshot=openapi_snapshot,
    )
    transaction_ids = [transaction.id for transaction in transactions]

    response = gateway_client.api_request(
        "POST",
        f"/v1/views/{saved_view.id}/pin",
        json=bulk_view_transaction_request(transaction_ids),
    )

    assert_response_matches_openapi(
        response,
        "bulkPinTransactions",
        httpx.codes.OK,
        openapi_snapshot,
    )
    result = json_object_response(response, httpx.codes.OK)
    assert int_field(result, "updatedCount") == len(transaction_ids)
    assert array_field(result, "notFoundIds") == []

    membership = _fetch_view_membership(
        gateway_client,
        saved_view.id,
        openapi_snapshot,
    )
    _assert_ids_present(array_field(membership, "pinned"), transaction_ids, "pinned")


@pytest.mark.openapi("excludeTransaction")
@pytest.mark.mutation
def test_exclude_transaction_excludes_matching_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "exclude-transaction",
        match_transaction=True,
        openapi_snapshot=openapi_snapshot,
    )

    response = gateway_client.api_request(
        "POST",
        f"/v1/views/{saved_view.id}/exclude/{transaction.id}",
    )

    assert_response_matches_openapi(
        response, "excludeTransaction", httpx.codes.OK, openapi_snapshot
    )
    updated = json_object_response(response, httpx.codes.OK)
    assert string_field(updated, "id") == saved_view.id
    assert int_field(updated, "excludedCount") >= 1

    membership = _fetch_view_membership(
        gateway_client,
        saved_view.id,
        openapi_snapshot,
    )
    _assert_id_present(array_field(membership, "excluded"), transaction.id, "excluded")


@pytest.mark.openapi("unexcludeTransaction")
@pytest.mark.mutation
def test_unexclude_transaction_removes_exclusion(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "unexclude-transaction",
        match_transaction=True,
        openapi_snapshot=openapi_snapshot,
    )
    exclude_response = gateway_client.api_request(
        "POST",
        f"/v1/views/{saved_view.id}/exclude/{transaction.id}",
    )
    assert_response_matches_openapi(
        exclude_response,
        "excludeTransaction",
        httpx.codes.OK,
        openapi_snapshot,
    )
    json_object_response(
        exclude_response,
        httpx.codes.OK,
    )

    response = gateway_client.api_request(
        "DELETE",
        f"/v1/views/{saved_view.id}/exclude/{transaction.id}",
    )

    assert_response_matches_openapi(
        response,
        "unexcludeTransaction",
        httpx.codes.OK,
        openapi_snapshot,
    )
    updated = json_object_response(response, httpx.codes.OK)
    assert string_field(updated, "id") == saved_view.id

    membership = _fetch_view_membership(
        gateway_client,
        saved_view.id,
        openapi_snapshot,
    )
    _assert_id_absent(array_field(membership, "excluded"), transaction.id, "excluded")
    _assert_id_present(array_field(membership, "matched"), transaction.id, "matched")


@pytest.mark.openapi("bulkExcludeTransactions")
@pytest.mark.mutation
def test_bulk_exclude_transactions_excludes_matching_transactions(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    saved_view, transactions = _view_with_imported_transactions(
        gateway_client,
        run_state,
        "bulk-exclude-transactions",
        match_transaction=True,
        openapi_snapshot=openapi_snapshot,
    )
    transaction_ids = [transaction.id for transaction in transactions]

    response = gateway_client.api_request(
        "POST",
        f"/v1/views/{saved_view.id}/exclude",
        json=bulk_view_transaction_request(transaction_ids),
    )

    assert_response_matches_openapi(
        response,
        "bulkExcludeTransactions",
        httpx.codes.OK,
        openapi_snapshot,
    )
    result = json_object_response(response, httpx.codes.OK)
    assert int_field(result, "updatedCount") == len(transaction_ids)
    assert array_field(result, "notFoundIds") == []

    membership = _fetch_view_membership(
        gateway_client,
        saved_view.id,
        openapi_snapshot,
    )
    _assert_ids_present(array_field(membership, "excluded"), transaction_ids, "excluded")


def _view_with_imported_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
    label: str,
    *,
    match_transaction: bool,
    openapi_snapshot: OpenApiDocument,
) -> tuple[CreatedSavedView, CreatedTransaction]:
    transaction = import_generated_transaction(
        gateway_client,
        run_state,
        label,
        openapi_snapshot=openapi_snapshot,
    )
    saved_view = create_saved_view(
        gateway_client,
        run_state,
        label,
        account_id=(
            transaction.account_id
            if match_transaction
            else generated_account_id(run_state, f"{label}-unmatched")
        ),
        openapi_snapshot=openapi_snapshot,
    )
    return saved_view, transaction


def _view_with_imported_transactions(
    gateway_client: GatewayClient,
    run_state: RunState,
    label: str,
    *,
    match_transaction: bool,
    openapi_snapshot: OpenApiDocument,
) -> tuple[CreatedSavedView, list[CreatedTransaction]]:
    account_id = generated_account_id(run_state, f"{label}-shared")
    transactions = [
        import_generated_transaction(
            gateway_client,
            run_state,
            f"{label}-1",
            account_id=account_id,
            openapi_snapshot=openapi_snapshot,
        ),
        import_generated_transaction(
            gateway_client,
            run_state,
            f"{label}-2",
            account_id=account_id,
            openapi_snapshot=openapi_snapshot,
        ),
    ]
    saved_view = create_saved_view(
        gateway_client,
        run_state,
        label,
        account_id=(
            account_id
            if match_transaction
            else generated_account_id(run_state, f"{label}-unmatched")
        ),
        openapi_snapshot=openapi_snapshot,
    )
    return saved_view, transactions


def _fetch_view_membership(
    gateway_client: GatewayClient,
    saved_view_id: str,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    response = gateway_client.api_request("GET", f"/v1/views/{saved_view_id}/transactions")
    assert_response_matches_openapi(
        response,
        "getViewTransactions",
        httpx.codes.OK,
        openapi_snapshot,
    )
    return json_object_response(response, httpx.codes.OK)


def _assert_ids_present(
    values: Sequence[object],
    expected_ids: Sequence[int],
    field_name: str,
) -> None:
    for expected_id in expected_ids:
        _assert_id_present(values, expected_id, field_name)


def _assert_id_present(values: Sequence[object], expected_id: int, field_name: str) -> None:
    assert expected_id in values, f"expected transaction {expected_id} in {field_name}: {values!r}"


def _assert_id_absent(values: Sequence[object], expected_id: int, field_name: str) -> None:
    assert expected_id not in values, (
        f"expected transaction {expected_id} to be absent from {field_name}: {values!r}"
    )
