from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import (
    JsonObject,
    array_field,
    assert_api_error,
    assert_no_content,
    int_field,
    json_array_response,
    json_object_response,
    object_item,
    string_field,
)
from api_tests.builders.views import (
    clone_saved_view_request,
    create_saved_view_request,
    update_saved_view_request,
    update_saved_view_transactions_request,
)
from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument
from api_tests.resources import create_manual_transaction
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
@pytest.mark.openapi("getView")
@pytest.mark.openapi("updateView")
@pytest.mark.openapi("getViewTransactions")
@pytest.mark.openapi("updateViewTransactions")
@pytest.mark.openapi("cloneView")
@pytest.mark.openapi("deleteView")
@pytest.mark.openapi("deleteTransaction")
@pytest.mark.destructive
@pytest.mark.mutation
def test_saved_view_lifecycle_persists_membership_clone_and_delete_behavior(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    first = create_manual_transaction(
        gateway_client, run_state, "view-lifecycle-1", openapi_snapshot=openapi_snapshot
    )
    second = create_manual_transaction(
        gateway_client, run_state, "view-lifecycle-2", openapi_snapshot=openapi_snapshot
    )

    create_payload = create_saved_view_request(
        run_state, "view-lifecycle", transaction_ids=(first.id,)
    )
    create_response = gateway_client.api_request("POST", "/v1/views", json=create_payload)
    assert_response_matches_openapi(
        create_response, "createView", httpx.codes.CREATED, openapi_snapshot
    )
    source = json_object_response(create_response, httpx.codes.CREATED)
    source_id = string_field(source, "id")
    assert source["name"] == create_payload["name"]
    assert int_field(source, "transactionCount") == 1
    assert _membership_ids(gateway_client, source_id, openapi_snapshot) == {first.id}

    rename_payload = update_saved_view_request(run_state, "view-lifecycle")
    rename_response = gateway_client.api_request(
        "PATCH", f"/v1/views/{source_id}", json=rename_payload
    )
    assert_response_matches_openapi(rename_response, "updateView", httpx.codes.OK, openapi_snapshot)
    assert (
        _fetch_view(gateway_client, source_id, openapi_snapshot)["name"] == rename_payload["name"]
    )

    delta_response = gateway_client.api_request(
        "PATCH",
        f"/v1/views/{source_id}/transactions",
        json=update_saved_view_transactions_request(
            add_transaction_ids=(second.id,), remove_transaction_ids=(first.id,)
        ),
    )
    assert_response_matches_openapi(
        delta_response, "updateViewTransactions", httpx.codes.NO_CONTENT, openapi_snapshot
    )
    assert_no_content(delta_response)
    assert _membership_ids(gateway_client, source_id, openapi_snapshot) == {second.id}
    assert (
        int_field(_fetch_view(gateway_client, source_id, openapi_snapshot), "transactionCount") == 1
    )

    clone_payload = clone_saved_view_request(run_state, "view-lifecycle-clone")
    clone_response = gateway_client.api_request(
        "POST", f"/v1/views/{source_id}/clone", json=clone_payload
    )
    assert_response_matches_openapi(
        clone_response, "cloneView", httpx.codes.CREATED, openapi_snapshot
    )
    clone = json_object_response(clone_response, httpx.codes.CREATED)
    clone_id = string_field(clone, "id")
    assert clone_id != source_id
    assert clone["name"] == clone_payload["name"]
    assert int_field(clone, "transactionCount") == 1
    assert _membership_ids(gateway_client, clone_id, openapi_snapshot) == {second.id}

    change_source = gateway_client.api_request(
        "PATCH",
        f"/v1/views/{source_id}/transactions",
        json=update_saved_view_transactions_request(add_transaction_ids=(first.id,)),
    )
    assert_response_matches_openapi(
        change_source, "updateViewTransactions", httpx.codes.NO_CONTENT, openapi_snapshot
    )
    assert_no_content(change_source)
    assert _membership_ids(gateway_client, source_id, openapi_snapshot) == {first.id, second.id}
    assert _membership_ids(gateway_client, clone_id, openapi_snapshot) == {second.id}

    delete_transaction_response = gateway_client.api_request(
        "DELETE", f"/v1/transactions/{second.id}"
    )
    assert_response_matches_openapi(
        delete_transaction_response,
        "deleteTransaction",
        httpx.codes.NO_CONTENT,
        openapi_snapshot,
    )
    assert_no_content(delete_transaction_response)
    assert _membership_ids(gateway_client, clone_id, openapi_snapshot) == set()
    assert (
        int_field(_fetch_view(gateway_client, clone_id, openapi_snapshot), "transactionCount") == 0
    )

    empty_delta_response = gateway_client.api_request(
        "PATCH",
        f"/v1/views/{source_id}/transactions",
        json={"addTransactionIds": [], "removeTransactionIds": []},
    )
    assert_api_error(empty_delta_response, httpx.codes.BAD_REQUEST, error_type="VALIDATION_ERROR")
    invalid_create_response = gateway_client.api_request(
        "POST",
        "/v1/views",
        json={"name": f"{run_state.run_id} invalid membership", "transactionIds": [0]},
    )
    assert_api_error(
        invalid_create_response, httpx.codes.BAD_REQUEST, error_type="VALIDATION_ERROR"
    )

    delete_response = gateway_client.api_request("DELETE", f"/v1/views/{source_id}")
    assert_response_matches_openapi(
        delete_response, "deleteView", httpx.codes.NO_CONTENT, openapi_snapshot
    )
    assert_no_content(delete_response)
    missing = gateway_client.api_request("GET", f"/v1/views/{source_id}")
    assert_response_matches_openapi(missing, "getView", httpx.codes.NOT_FOUND, openapi_snapshot)
    assert_api_error(missing, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")


def _fetch_view_membership(
    gateway_client: GatewayClient,
    saved_view_id: str,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    response = gateway_client.api_request("GET", f"/v1/views/{saved_view_id}/transactions")
    assert_response_matches_openapi(
        response, "getViewTransactions", httpx.codes.OK, openapi_snapshot
    )
    return json_object_response(response, httpx.codes.OK)


def _membership_ids(
    gateway_client: GatewayClient,
    saved_view_id: str,
    openapi_snapshot: OpenApiDocument,
) -> set[int]:
    membership = _fetch_view_membership(gateway_client, saved_view_id, openapi_snapshot)
    ids = array_field(membership, "transactionIds")
    assert all(isinstance(item, int) for item in ids)
    return {item for item in ids if isinstance(item, int)}


def _fetch_view(
    gateway_client: GatewayClient,
    saved_view_id: str,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    response = gateway_client.api_request("GET", f"/v1/views/{saved_view_id}")
    assert_response_matches_openapi(response, "getView", httpx.codes.OK, openapi_snapshot)
    return json_object_response(response, httpx.codes.OK)
