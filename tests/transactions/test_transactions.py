from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import (
    array_field,
    assert_no_content,
    int_field,
    json_array_response,
    json_object_response,
    json_scalar_response,
    object_field,
    object_item,
    string_field,
)
from api_tests.builders.transactions import (
    batch_import_request,
    bulk_delete_request,
    generated_account_id,
    import_transaction_request,
    transaction_update_request,
)
from api_tests.client import GatewayClient
from api_tests.resources import (
    import_generated_transaction,
    preview_transactions,
    save_csv_wizard_statement_format,
)
from api_tests.run_state import RunState


@pytest.mark.openapi("getTransactions")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_transactions_returns_current_user_transactions(gateway_client: GatewayClient) -> None:
    response = gateway_client.api_request("GET", "/v1/transactions")

    transactions = json_array_response(response, httpx.codes.OK)
    for item in transactions:
        transaction = object_item(item)
        int_field(transaction, "id")
        string_field(transaction, "description")


@pytest.mark.openapi("searchTransactions")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_search_transactions_returns_paged_response(gateway_client: GatewayClient) -> None:
    response = gateway_client.api_request(
        "GET",
        "/v1/transactions/search",
        params={
            "page": 0,
            "size": 10,
            "sort": ["date,DESC", "id,DESC"],
        },
    )

    page = json_object_response(response, httpx.codes.OK)
    array_field(page, "content")
    object_field(page, "metadata")


@pytest.mark.openapi("countTransactions")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_count_transactions_returns_integer(gateway_client: GatewayClient) -> None:
    response = gateway_client.api_request(
        "GET",
        "/v1/transactions/count",
        params={
            "dateFrom": "1900-01-01",
            "dateTo": "2999-12-31",
        },
    )

    count = json_scalar_response(response, httpx.codes.OK)
    assert isinstance(count, int)


@pytest.mark.openapi("previewTransactions")
@pytest.mark.mutation
def test_preview_transactions_returns_import_token_and_preview_rows(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    statement_format = save_csv_wizard_statement_format(gateway_client, run_state, "preview-tx")
    account_id = generated_account_id(run_state, "preview-tx")

    preview = preview_transactions(
        gateway_client,
        run_state,
        "preview-tx",
        statement_format_id=statement_format.id,
        account_id=account_id,
    )

    string_field(preview, "previewImportToken")
    assert int_field(preview, "statementFormatId") == statement_format.id
    assert array_field(preview, "transactions")


@pytest.mark.openapi("batchImportTransactions")
@pytest.mark.destructive
@pytest.mark.mutation
def test_batch_import_transactions_creates_generated_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    statement_format = save_csv_wizard_statement_format(gateway_client, run_state, "batch-tx")
    account_id = generated_account_id(run_state, "batch-tx")
    preview = preview_transactions(
        gateway_client,
        run_state,
        "batch-tx",
        statement_format_id=statement_format.id,
        account_id=account_id,
    )

    response = gateway_client.api_request(
        "POST",
        "/v1/transactions/batch",
        json=batch_import_request(
            preview_import_token=string_field(preview, "previewImportToken"),
            transactions=[import_transaction_request(run_state, "batch-tx", account_id=account_id)],
        ),
    )

    result = json_object_response(response, httpx.codes.OK)
    assert int_field(result, "created") >= 1
    assert array_field(result, "transactions")


@pytest.mark.openapi("getTransaction")
@pytest.mark.mutation
def test_get_transaction_returns_imported_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = import_generated_transaction(gateway_client, run_state, "get-tx")

    response = gateway_client.api_request("GET", f"/v1/transactions/{created.id}")

    transaction = json_object_response(response, httpx.codes.OK)
    assert int_field(transaction, "id") == created.id
    assert string_field(transaction, "accountId") == created.account_id


@pytest.mark.openapi("updateTransaction")
@pytest.mark.mutation
def test_update_transaction_updates_mutable_fields(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = import_generated_transaction(gateway_client, run_state, "update-tx")
    update_payload = transaction_update_request(run_state, "update-tx")

    response = gateway_client.api_request(
        "PATCH",
        f"/v1/transactions/{created.id}",
        json=update_payload,
    )

    transaction = json_object_response(response, httpx.codes.OK)
    assert int_field(transaction, "id") == created.id
    assert transaction["description"] == update_payload["description"]
    assert transaction["accountId"] == update_payload["accountId"]


@pytest.mark.openapi("deleteTransaction")
@pytest.mark.destructive
@pytest.mark.mutation
def test_delete_transaction_soft_deletes_imported_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = import_generated_transaction(gateway_client, run_state, "delete-tx")

    response = gateway_client.api_request("DELETE", f"/v1/transactions/{created.id}")

    assert_no_content(response)


@pytest.mark.openapi("bulkDeleteTransactions")
@pytest.mark.destructive
@pytest.mark.mutation
def test_bulk_delete_transactions_deletes_imported_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = import_generated_transaction(gateway_client, run_state, "bulk-delete-tx")

    response = gateway_client.api_request(
        "POST",
        "/v1/transactions/bulk-delete",
        json=bulk_delete_request([created.id]),
    )

    result = json_object_response(response, httpx.codes.OK)
    assert int_field(result, "deletedCount") >= 1
    array_field(result, "notFoundIds")
