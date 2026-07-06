from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import (
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
from api_tests.resources import (
    CreatedSavedView,
    CreatedTransaction,
    create_saved_view,
    import_generated_transaction,
)
from api_tests.run_state import RunState


@pytest.mark.openapi("listViews")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_list_views_returns_saved_views(gateway_client: GatewayClient) -> None:
    response = gateway_client.api_request("GET", "/v1/views")

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
) -> None:
    payload = create_saved_view_request(run_state, "create-view", search_text=run_state.run_id)

    response = gateway_client.api_request("POST", "/v1/views", json=payload)

    saved_view = json_object_response(response, httpx.codes.CREATED)
    assert saved_view["name"] == payload["name"]
    string_field(saved_view, "id")


@pytest.mark.openapi("getView")
@pytest.mark.mutation
def test_get_view_returns_created_saved_view(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = create_saved_view(gateway_client, run_state, "get-view")

    response = gateway_client.api_request("GET", f"/v1/views/{created.id}")

    saved_view = json_object_response(response, httpx.codes.OK)
    assert string_field(saved_view, "id") == created.id
    assert saved_view["name"] == created.body["name"]


@pytest.mark.openapi("updateView")
@pytest.mark.mutation
def test_update_view_updates_created_saved_view(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = create_saved_view(gateway_client, run_state, "update-view")
    payload = update_saved_view_request(run_state, "update-view")

    response = gateway_client.api_request("PUT", f"/v1/views/{created.id}", json=payload)

    saved_view = json_object_response(response, httpx.codes.OK)
    assert string_field(saved_view, "id") == created.id
    assert saved_view["name"] == payload["name"]


@pytest.mark.openapi("deleteView")
@pytest.mark.destructive
@pytest.mark.mutation
def test_delete_view_removes_created_saved_view(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = create_saved_view(gateway_client, run_state, "delete-view")

    response = gateway_client.api_request("DELETE", f"/v1/views/{created.id}")

    assert_no_content(response)


@pytest.mark.openapi("getViewTransactions")
@pytest.mark.mutation
def test_get_view_transactions_returns_membership_groups(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    saved_view, _transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "view-transactions",
        match_transaction=True,
    )

    response = gateway_client.api_request("GET", f"/v1/views/{saved_view.id}/transactions")

    membership = json_object_response(response, httpx.codes.OK)
    array_field(membership, "matched")
    array_field(membership, "pinned")
    array_field(membership, "excluded")


@pytest.mark.openapi("pinTransaction")
@pytest.mark.mutation
def test_pin_transaction_adds_transaction_to_view(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "pin-transaction",
        match_transaction=False,
    )

    response = gateway_client.api_request(
        "POST",
        f"/v1/views/{saved_view.id}/pin/{transaction.id}",
    )

    updated = json_object_response(response, httpx.codes.OK)
    assert string_field(updated, "id") == saved_view.id
    assert int_field(updated, "pinnedCount") >= 1


@pytest.mark.openapi("unpinTransaction")
@pytest.mark.mutation
def test_unpin_transaction_removes_transaction_pin(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "unpin-transaction",
        match_transaction=False,
    )
    json_object_response(
        gateway_client.api_request("POST", f"/v1/views/{saved_view.id}/pin/{transaction.id}"),
        httpx.codes.OK,
    )

    response = gateway_client.api_request(
        "DELETE",
        f"/v1/views/{saved_view.id}/pin/{transaction.id}",
    )

    updated = json_object_response(response, httpx.codes.OK)
    assert string_field(updated, "id") == saved_view.id


@pytest.mark.openapi("bulkPinTransactions")
@pytest.mark.mutation
def test_bulk_pin_transactions_adds_transaction_to_view(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "bulk-pin-transactions",
        match_transaction=False,
    )

    response = gateway_client.api_request(
        "POST",
        f"/v1/views/{saved_view.id}/pin",
        json=bulk_view_transaction_request(transaction.id),
    )

    result = json_object_response(response, httpx.codes.OK)
    assert int_field(result, "updatedCount") >= 1
    array_field(result, "notFoundIds")


@pytest.mark.openapi("excludeTransaction")
@pytest.mark.mutation
def test_exclude_transaction_excludes_matching_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "exclude-transaction",
        match_transaction=True,
    )

    response = gateway_client.api_request(
        "POST",
        f"/v1/views/{saved_view.id}/exclude/{transaction.id}",
    )

    updated = json_object_response(response, httpx.codes.OK)
    assert string_field(updated, "id") == saved_view.id
    assert int_field(updated, "excludedCount") >= 1


@pytest.mark.openapi("unexcludeTransaction")
@pytest.mark.mutation
def test_unexclude_transaction_removes_exclusion(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "unexclude-transaction",
        match_transaction=True,
    )
    json_object_response(
        gateway_client.api_request(
            "POST",
            f"/v1/views/{saved_view.id}/exclude/{transaction.id}",
        ),
        httpx.codes.OK,
    )

    response = gateway_client.api_request(
        "DELETE",
        f"/v1/views/{saved_view.id}/exclude/{transaction.id}",
    )

    updated = json_object_response(response, httpx.codes.OK)
    assert string_field(updated, "id") == saved_view.id


@pytest.mark.openapi("bulkExcludeTransactions")
@pytest.mark.mutation
def test_bulk_exclude_transactions_excludes_matching_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    saved_view, transaction = _view_with_imported_transaction(
        gateway_client,
        run_state,
        "bulk-exclude-transactions",
        match_transaction=True,
    )

    response = gateway_client.api_request(
        "POST",
        f"/v1/views/{saved_view.id}/exclude",
        json=bulk_view_transaction_request(transaction.id),
    )

    result = json_object_response(response, httpx.codes.OK)
    assert int_field(result, "updatedCount") >= 1
    array_field(result, "notFoundIds")


def _view_with_imported_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
    label: str,
    *,
    match_transaction: bool,
) -> tuple[CreatedSavedView, CreatedTransaction]:
    transaction = import_generated_transaction(gateway_client, run_state, label)
    saved_view = create_saved_view(
        gateway_client,
        run_state,
        label,
        account_id=(
            transaction.account_id
            if match_transaction
            else generated_account_id(run_state, f"{label}-unmatched")
        ),
    )
    return saved_view, transaction
