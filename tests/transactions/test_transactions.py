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
from api_tests.openapi import OpenApiDocument
from api_tests.resources import (
    import_generated_transaction,
    preview_transactions,
    save_csv_wizard_statement_format,
)
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi


@pytest.mark.openapi("getTransactions")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_transactions_returns_current_user_transactions(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request("GET", "/v1/transactions")

    assert_response_matches_openapi(response, "getTransactions", httpx.codes.OK, openapi_snapshot)
    transactions = json_array_response(response, httpx.codes.OK)
    for item in transactions:
        transaction = object_item(item)
        int_field(transaction, "id")
        string_field(transaction, "description")


@pytest.mark.openapi("searchTransactions")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_search_transactions_returns_paged_response(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "GET",
        "/v1/transactions/search",
        params={
            "page": 0,
            "size": 10,
            "sort": ["date,DESC", "id,DESC"],
        },
    )

    assert_response_matches_openapi(
        response, "searchTransactions", httpx.codes.OK, openapi_snapshot
    )
    page = json_object_response(response, httpx.codes.OK)
    array_field(page, "content")
    object_field(page, "metadata")


@pytest.mark.openapi("countTransactions")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_count_transactions_returns_integer(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "GET",
        "/v1/transactions/count",
        params={
            "dateFrom": "1900-01-01",
            "dateTo": "2999-12-31",
        },
    )

    assert_response_matches_openapi(response, "countTransactions", httpx.codes.OK, openapi_snapshot)
    count = json_scalar_response(response, httpx.codes.OK)
    assert isinstance(count, int)


@pytest.mark.openapi("previewTransactions")
@pytest.mark.mutation
def test_preview_transactions_returns_import_token_and_preview_rows(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    statement_format = save_csv_wizard_statement_format(
        gateway_client,
        run_state,
        "preview-tx",
        openapi_snapshot=openapi_snapshot,
    )
    account_id = generated_account_id(run_state, "preview-tx")

    preview = preview_transactions(
        gateway_client,
        run_state,
        "preview-tx",
        statement_format_id=statement_format.id,
        account_id=account_id,
        openapi_snapshot=openapi_snapshot,
    )

    string_field(preview, "previewImportToken")
    assert int_field(preview, "statementFormatId") == statement_format.id
    preview_rows = array_field(preview, "transactions")
    assert len(preview_rows) == 2

    debit = object_item(preview_rows[0])
    assert debit["date"] == "2026-01-15"
    assert debit["description"] == f"{run_state.run_id} preview-tx debit"
    assert debit["amount"] == 12.34
    assert debit["type"] == "DEBIT"
    assert debit["category"] == "API Test"
    assert debit["bankName"] == f"{run_state.run_id} preview-tx Bank"
    assert debit["currencyIsoCode"] == "USD"
    if "accountId" in debit:
        assert debit["accountId"] == account_id

    credit = object_item(preview_rows[1])
    assert credit["date"] == "2026-01-16"
    assert credit["description"] == f"{run_state.run_id} preview-tx credit"
    assert credit["amount"] == 5.67
    assert credit["type"] == "CREDIT"
    assert credit["category"] == "API Test"
    assert credit["bankName"] == f"{run_state.run_id} preview-tx Bank"
    assert credit["currencyIsoCode"] == "USD"
    if "accountId" in credit:
        assert credit["accountId"] == account_id


@pytest.mark.openapi("batchImportTransactions")
@pytest.mark.destructive
@pytest.mark.mutation
def test_batch_import_transactions_creates_generated_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    statement_format = save_csv_wizard_statement_format(
        gateway_client,
        run_state,
        "batch-tx",
        openapi_snapshot=openapi_snapshot,
    )
    account_id = generated_account_id(run_state, "batch-tx")
    preview = preview_transactions(
        gateway_client,
        run_state,
        "batch-tx",
        statement_format_id=statement_format.id,
        account_id=account_id,
        openapi_snapshot=openapi_snapshot,
    )

    import_requests = [
        import_transaction_request(run_state, "batch-tx-1", account_id=account_id),
        import_transaction_request(run_state, "batch-tx-2", account_id=account_id),
    ]

    response = gateway_client.api_request(
        "POST",
        "/v1/transactions/batch",
        json=batch_import_request(
            preview_import_token=string_field(preview, "previewImportToken"),
            transactions=import_requests,
        ),
    )

    assert_response_matches_openapi(
        response,
        "batchImportTransactions",
        httpx.codes.OK,
        openapi_snapshot,
    )
    result = json_object_response(response, httpx.codes.OK)
    assert int_field(result, "created") == len(import_requests)
    transactions = array_field(result, "transactions")
    assert len(transactions) == len(import_requests)


@pytest.mark.openapi("getTransaction")
@pytest.mark.mutation
def test_get_transaction_returns_imported_transaction(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = import_generated_transaction(
        gateway_client,
        run_state,
        "get-tx",
        openapi_snapshot=openapi_snapshot,
    )

    response = gateway_client.api_request("GET", f"/v1/transactions/{created.id}")

    assert_response_matches_openapi(response, "getTransaction", httpx.codes.OK, openapi_snapshot)
    transaction = json_object_response(response, httpx.codes.OK)
    assert int_field(transaction, "id") == created.id
    assert string_field(transaction, "accountId") == created.account_id


@pytest.mark.openapi("updateTransaction")
@pytest.mark.mutation
def test_update_transaction_updates_mutable_fields(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = import_generated_transaction(
        gateway_client,
        run_state,
        "update-tx",
        openapi_snapshot=openapi_snapshot,
    )
    update_payload = transaction_update_request(run_state, "update-tx")

    response = gateway_client.api_request(
        "PATCH",
        f"/v1/transactions/{created.id}",
        json=update_payload,
    )

    assert_response_matches_openapi(response, "updateTransaction", httpx.codes.OK, openapi_snapshot)
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
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = import_generated_transaction(
        gateway_client,
        run_state,
        "delete-tx",
        openapi_snapshot=openapi_snapshot,
    )

    response = gateway_client.api_request("DELETE", f"/v1/transactions/{created.id}")

    assert_response_matches_openapi(
        response,
        "deleteTransaction",
        httpx.codes.NO_CONTENT,
        openapi_snapshot,
    )
    assert_no_content(response)

    get_response = gateway_client.api_request("GET", f"/v1/transactions/{created.id}")
    assert_response_matches_openapi(
        get_response,
        "getTransaction",
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    json_object_response(get_response, httpx.codes.NOT_FOUND)


@pytest.mark.openapi("bulkDeleteTransactions")
@pytest.mark.destructive
@pytest.mark.mutation
def test_bulk_delete_transactions_deletes_imported_transactions(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    first = import_generated_transaction(
        gateway_client,
        run_state,
        "bulk-delete-tx-1",
        openapi_snapshot=openapi_snapshot,
    )
    second = import_generated_transaction(
        gateway_client,
        run_state,
        "bulk-delete-tx-2",
        openapi_snapshot=openapi_snapshot,
    )
    transaction_ids = [first.id, second.id]

    response = gateway_client.api_request(
        "POST",
        "/v1/transactions/bulk-delete",
        json=bulk_delete_request(transaction_ids),
    )

    assert_response_matches_openapi(
        response,
        "bulkDeleteTransactions",
        httpx.codes.OK,
        openapi_snapshot,
    )
    result = json_object_response(response, httpx.codes.OK)
    assert int_field(result, "deletedCount") == len(transaction_ids)
    assert array_field(result, "notFoundIds") == []

    for transaction_id in transaction_ids:
        get_response = gateway_client.api_request("GET", f"/v1/transactions/{transaction_id}")
        assert_response_matches_openapi(
            get_response,
            "getTransaction",
            httpx.codes.NOT_FOUND,
            openapi_snapshot,
        )
        json_object_response(get_response, httpx.codes.NOT_FOUND)


@pytest.mark.openapi("getTransaction")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_transaction_unknown_id_returns_not_found(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request("GET", "/v1/transactions/9223372036854775807")

    assert_response_matches_openapi(
        response,
        "getTransaction",
        httpx.codes.NOT_FOUND,
        openapi_snapshot,
    )
    json_object_response(response, httpx.codes.NOT_FOUND)
