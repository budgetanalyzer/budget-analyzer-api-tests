from __future__ import annotations

from collections.abc import Sequence

import httpx
import pytest

from api_tests.assertions import (
    JsonObject,
    array_field,
    assert_api_error,
    json_array_response,
    json_object_response,
    object_item,
    string_field,
)
from api_tests.builders.statement_formats import update_statement_format_request
from api_tests.builders.transactions import (
    batch_import_file_request,
    batch_import_request,
    generated_account_id,
    import_transaction_request,
    transaction_update_request,
)
from api_tests.builders.views import (
    clone_saved_view_request,
    update_saved_view_request,
    update_saved_view_transactions_request,
)
from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument
from api_tests.resources import (
    create_manual_transaction,
    create_saved_view,
    preview_transaction_files,
    save_csv_wizard_statement_format,
)
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi


@pytest.mark.authorization
@pytest.mark.readonly
def test_primary_and_secondary_sessions_are_distinct(
    distinct_user_sessions: None,
) -> None:
    # Browser-backed sessions have each already passed GET /auth/v1/user during
    # acquisition. The fixture rejects a reused opaque session before any
    # ownership assertion can produce a false result.
    assert distinct_user_sessions is None


@pytest.mark.openapi("createTransaction")
@pytest.mark.openapi("getTransaction")
@pytest.mark.openapi("updateTransaction")
@pytest.mark.authorization
@pytest.mark.mutation
def test_secondary_cannot_read_or_update_primary_transaction(
    gateway_client: GatewayClient,
    secondary_gateway_client: GatewayClient,
    distinct_user_sessions: None,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    assert distinct_user_sessions is None
    created = create_manual_transaction(
        gateway_client,
        run_state,
        "authorization-primary-transaction",
        openapi_snapshot=openapi_snapshot,
    )

    read_response = secondary_gateway_client.api_request("GET", f"/v1/transactions/{created.id}")
    assert_response_matches_openapi(
        read_response,
        "getTransaction",
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    assert_api_error(read_response, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")

    update_response = secondary_gateway_client.api_request(
        "PATCH",
        f"/v1/transactions/{created.id}",
        json=transaction_update_request(run_state, "authorization-foreign-transaction"),
    )
    assert_response_matches_openapi(
        update_response,
        "updateTransaction",
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    assert_api_error(update_response, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")

    persisted = _get_transaction(gateway_client, created.id, openapi_snapshot)
    _assert_fields_unchanged(
        persisted,
        created.body,
        ("id", "description", "accountId", "amount", "date", "type", "currencyIsoCode"),
    )


@pytest.mark.openapi("saveCsvWizardFormat")
@pytest.mark.openapi("getFormat")
@pytest.mark.openapi("updateFormat")
@pytest.mark.openapi("hideFormat")
@pytest.mark.authorization
@pytest.mark.destructive
@pytest.mark.mutation
def test_secondary_cannot_read_update_or_hide_primary_user_format(
    gateway_client: GatewayClient,
    secondary_gateway_client: GatewayClient,
    distinct_user_sessions: None,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    assert distinct_user_sessions is None
    created = save_csv_wizard_statement_format(
        gateway_client,
        run_state,
        "authorization-primary-format",
        openapi_snapshot=openapi_snapshot,
    )

    read_response = secondary_gateway_client.api_request(
        "GET", f"/v1/statement-formats/{created.id}"
    )
    assert_response_matches_openapi(
        read_response,
        "getFormat",
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    assert_api_error(read_response, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")

    update_response = secondary_gateway_client.api_request(
        "PUT",
        f"/v1/statement-formats/{created.id}",
        json=update_statement_format_request(run_state, "authorization-foreign-format"),
    )
    assert_response_matches_openapi(
        update_response,
        "updateFormat",
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    assert_api_error(update_response, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")

    hide_response = secondary_gateway_client.api_request(
        "POST", f"/v1/statement-formats/{created.id}/hide"
    )
    assert_response_matches_openapi(
        hide_response,
        "hideFormat",
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    assert_api_error(hide_response, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")

    persisted = _get_statement_format(gateway_client, created.id, openapi_snapshot)
    _assert_fields_unchanged(
        persisted,
        created.body,
        ("id", "displayName", "bankName", "formatType", "defaultCurrencyIsoCode"),
    )
    assert persisted.get("hidden") in {None, False}


@pytest.mark.openapi("createTransaction")
@pytest.mark.openapi("createView")
@pytest.mark.openapi("listViews")
@pytest.mark.openapi("getView")
@pytest.mark.openapi("updateView")
@pytest.mark.openapi("getViewTransactions")
@pytest.mark.openapi("updateViewTransactions")
@pytest.mark.openapi("cloneView")
@pytest.mark.authorization
@pytest.mark.mutation
def test_cross_user_saved_view_operations_are_rejected_atomically(
    gateway_client: GatewayClient,
    secondary_gateway_client: GatewayClient,
    distinct_user_sessions: None,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    assert distinct_user_sessions is None
    primary_transaction = create_manual_transaction(
        gateway_client,
        run_state,
        "authorization-primary-view-member",
        openapi_snapshot=openapi_snapshot,
    )
    primary_view = create_saved_view(
        gateway_client,
        run_state,
        "authorization-primary",
        transaction_ids=(primary_transaction.id,),
        openapi_snapshot=openapi_snapshot,
    )
    primary_name = string_field(primary_view.body, "name")

    _assert_not_found(
        secondary_gateway_client.api_request("GET", f"/v1/views/{primary_view.id}"),
        "getView",
        openapi_snapshot,
    )
    _assert_not_found(
        secondary_gateway_client.api_request(
            "PATCH",
            f"/v1/views/{primary_view.id}",
            json=update_saved_view_request(run_state, "authorization-foreign-primary"),
        ),
        "updateView",
        openapi_snapshot,
    )

    secondary_first = create_manual_transaction(
        secondary_gateway_client,
        run_state,
        "authorization-secondary-view-member-1",
        openapi_snapshot=openapi_snapshot,
    )
    secondary_second = create_manual_transaction(
        secondary_gateway_client,
        run_state,
        "authorization-secondary-view-member-2",
        openapi_snapshot=openapi_snapshot,
    )
    secondary_view = create_saved_view(
        secondary_gateway_client,
        run_state,
        "authorization-secondary",
        transaction_ids=(secondary_first.id,),
        openapi_snapshot=openapi_snapshot,
    )

    primary_membership_response = secondary_gateway_client.api_request(
        "PATCH",
        f"/v1/views/{primary_view.id}/transactions",
        json=update_saved_view_transactions_request(add_transaction_ids=(secondary_second.id,)),
    )
    _assert_not_found(
        primary_membership_response,
        "updateViewTransactions",
        openapi_snapshot,
    )

    invalid_create_name = f"{run_state.run_id} authorization foreign member view"
    invalid_create_response = secondary_gateway_client.api_request(
        "POST",
        "/v1/views",
        json={"name": invalid_create_name, "transactionIds": [primary_transaction.id]},
    )
    _assert_membership_stale(invalid_create_response, "createView", openapi_snapshot)

    mixed_update_response = secondary_gateway_client.api_request(
        "PATCH",
        f"/v1/views/{secondary_view.id}/transactions",
        json=update_saved_view_transactions_request(
            add_transaction_ids=(secondary_second.id, primary_transaction.id)
        ),
    )
    _assert_membership_stale(
        mixed_update_response,
        "updateViewTransactions",
        openapi_snapshot,
    )

    clone_payload = clone_saved_view_request(run_state, "authorization-foreign-primary")
    clone_response = secondary_gateway_client.api_request(
        "POST",
        f"/v1/views/{primary_view.id}/clone",
        json=clone_payload,
    )
    _assert_not_found(clone_response, "cloneView", openapi_snapshot)

    assert _get_view_membership(gateway_client, primary_view.id, openapi_snapshot) == {
        primary_transaction.id
    }
    persisted_primary = _get_view(gateway_client, primary_view.id, openapi_snapshot)
    assert string_field(persisted_primary, "name") == primary_name
    assert _get_view_membership(secondary_gateway_client, secondary_view.id, openapi_snapshot) == {
        secondary_first.id
    }

    secondary_view_names = _list_view_names(secondary_gateway_client, openapi_snapshot)
    assert invalid_create_name not in secondary_view_names
    assert clone_payload["name"] not in secondary_view_names


@pytest.mark.openapi("saveCsvWizardFormat")
@pytest.mark.openapi("previewTransactions")
@pytest.mark.openapi("batchImportTransactions")
@pytest.mark.openapi("getTransactions")
@pytest.mark.authorization
@pytest.mark.destructive
@pytest.mark.mutation
def test_primary_preview_token_cannot_authorize_secondary_import(
    gateway_client: GatewayClient,
    secondary_gateway_client: GatewayClient,
    distinct_user_sessions: None,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    assert distinct_user_sessions is None
    statement_format = save_csv_wizard_statement_format(
        gateway_client,
        run_state,
        "authorization-preview-format",
        openapi_snapshot=openapi_snapshot,
    )
    account_id = generated_account_id(run_state, "authorization-preview-owner")
    preview = preview_transaction_files(
        gateway_client,
        run_state,
        ("authorization-primary-preview",),
        statement_format_id=statement_format.id,
        account_id=account_id,
        openapi_snapshot=openapi_snapshot,
    )
    preview_file = object_item(array_field(preview, "files")[0])
    transaction = import_transaction_request(
        run_state,
        "authorization-secondary-import",
        account_id=account_id,
    )

    import_response = secondary_gateway_client.api_request(
        "POST",
        "/v1/transactions/batch",
        json=batch_import_request(
            [
                batch_import_file_request(
                    preview_import_token=string_field(preview_file, "previewImportToken"),
                    transactions=[transaction],
                )
            ]
        ),
    )
    assert_response_matches_openapi(
        import_response,
        "batchImportTransactions",
        httpx.codes.UNPROCESSABLE_ENTITY,
        openapi_snapshot,
    )
    assert_api_error(
        import_response,
        httpx.codes.UNPROCESSABLE_ENTITY,
        error_type="APPLICATION_ERROR",
        code="PREVIEW_IMPORT_TOKEN_INVALID",
    )

    assert not _transactions_for_account(gateway_client, account_id, openapi_snapshot)
    assert not _transactions_for_account(
        secondary_gateway_client,
        account_id,
        openapi_snapshot,
    )


def _get_transaction(
    client: GatewayClient,
    transaction_id: int,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    response = client.api_request("GET", f"/v1/transactions/{transaction_id}")
    assert_response_matches_openapi(response, "getTransaction", httpx.codes.OK, openapi_snapshot)
    return json_object_response(response, httpx.codes.OK)


def _get_statement_format(
    client: GatewayClient,
    statement_format_id: int,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    response = client.api_request("GET", f"/v1/statement-formats/{statement_format_id}")
    assert_response_matches_openapi(response, "getFormat", httpx.codes.OK, openapi_snapshot)
    return json_object_response(response, httpx.codes.OK)


def _get_view(
    client: GatewayClient,
    view_id: str,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    response = client.api_request("GET", f"/v1/views/{view_id}")
    assert_response_matches_openapi(response, "getView", httpx.codes.OK, openapi_snapshot)
    return json_object_response(response, httpx.codes.OK)


def _get_view_membership(
    client: GatewayClient,
    view_id: str,
    openapi_snapshot: OpenApiDocument,
) -> set[int]:
    response = client.api_request("GET", f"/v1/views/{view_id}/transactions")
    assert_response_matches_openapi(
        response,
        "getViewTransactions",
        httpx.codes.OK,
        openapi_snapshot,
    )
    body = json_object_response(response, httpx.codes.OK)
    transaction_ids = array_field(body, "transactionIds")
    assert all(isinstance(item, int) for item in transaction_ids)
    return {item for item in transaction_ids if isinstance(item, int)}


def _list_view_names(
    client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> set[str]:
    response = client.api_request("GET", "/v1/views")
    assert_response_matches_openapi(response, "listViews", httpx.codes.OK, openapi_snapshot)
    return {
        string_field(object_item(item), "name")
        for item in json_array_response(response, httpx.codes.OK)
    }


def _transactions_for_account(
    client: GatewayClient,
    account_id: str,
    openapi_snapshot: OpenApiDocument,
) -> list[JsonObject]:
    response = client.api_request("GET", "/v1/transactions")
    assert_response_matches_openapi(response, "getTransactions", httpx.codes.OK, openapi_snapshot)
    return [
        transaction
        for item in json_array_response(response, httpx.codes.OK)
        if (transaction := object_item(item)).get("accountId") == account_id
    ]


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


def _assert_membership_stale(
    response: httpx.Response,
    operation_id: str,
    openapi_snapshot: OpenApiDocument,
) -> None:
    assert_response_matches_openapi(
        response,
        operation_id,
        httpx.codes.UNPROCESSABLE_ENTITY,
        openapi_snapshot,
    )
    assert_api_error(
        response,
        httpx.codes.UNPROCESSABLE_ENTITY,
        error_type="APPLICATION_ERROR",
        code="SAVED_VIEW_MEMBERSHIP_STALE",
    )


def _assert_fields_unchanged(
    actual: JsonObject,
    expected: JsonObject,
    field_names: Sequence[str],
) -> None:
    for field_name in field_names:
        assert actual.get(field_name) == expected.get(field_name)
