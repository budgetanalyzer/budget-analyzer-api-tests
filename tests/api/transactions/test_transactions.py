from __future__ import annotations

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
    json_scalar_response,
    object_item,
    string_field,
)
from api_tests.builders.transactions import (
    batch_import_file_request,
    batch_import_request,
    bulk_delete_request,
    generated_account_id,
    import_transaction_request,
    transaction_update_request,
)
from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument
from api_tests.resources import (
    create_manual_transaction,
    preview_transaction_files,
    save_csv_wizard_statement_format,
)
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi


@pytest.fixture(scope="module")
def transaction_statement_format_id(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> int:
    return save_csv_wizard_statement_format(
        gateway_client,
        run_state,
        "transaction-import-format",
        openapi_snapshot=openapi_snapshot,
    ).id


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
        params={"dateFrom": "1900-01-01", "dateTo": "2999-12-31"},
    )

    assert_response_matches_openapi(response, "countTransactions", httpx.codes.OK, openapi_snapshot)
    count = json_scalar_response(response, httpx.codes.OK)
    assert isinstance(count, int)


@pytest.mark.openapi("createTransaction")
@pytest.mark.openapi("getTransaction")
@pytest.mark.openapi("updateTransaction")
@pytest.mark.openapi("deleteTransaction")
@pytest.mark.openapi("countTransactions")
@pytest.mark.destructive
@pytest.mark.mutation
def test_transaction_crud_persists_exact_fields_and_filter_binding(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    account_id = generated_account_id(run_state, "crud-filter")
    created = create_manual_transaction(
        gateway_client,
        run_state,
        "crud-debit",
        account_id=account_id,
        amount=11.11,
        openapi_snapshot=openapi_snapshot,
    )
    second = create_manual_transaction(
        gateway_client,
        run_state,
        "crud-credit",
        account_id=account_id,
        amount=22.22,
        transaction_type="CREDIT",
        date="2026-01-16",
        openapi_snapshot=openapi_snapshot,
    )
    assert created.body["description"] == f"{run_state.run_id} crud-debit transaction"
    assert created.body["accountId"] == created.account_id
    assert created.body["currencyIsoCode"] == "USD"
    assert created.body["amount"] == 11.11
    assert created.body["bankName"] == f"{run_state.run_id} crud-debit Bank"

    expected_counts = (
        ({"accountId": account_id}, 2),
        ({"accountId": account_id, "type": "DEBIT"}, 1),
        ({"accountId": account_id, "dateFrom": "2026-01-16"}, 1),
        ({"accountId": account_id, "minAmount": 20, "maxAmount": 23}, 1),
    )
    for query, expected in expected_counts:
        count_response = gateway_client.api_request("GET", "/v1/transactions/count", params=query)
        assert_response_matches_openapi(
            count_response, "countTransactions", httpx.codes.OK, openapi_snapshot
        )
        assert json_scalar_response(count_response, httpx.codes.OK) == expected
    controlled = {
        int_field(item, "id"): item
        for item in _fetch_transactions(gateway_client, openapi_snapshot)
        if item.get("accountId") == account_id
    }
    assert set(controlled) == {created.id, second.id}
    _assert_transaction(controlled[created.id], created.body)
    _assert_transaction(controlled[second.id], second.body)

    update_payload = transaction_update_request(run_state, "crud-debit")
    update_response = gateway_client.api_request(
        "PATCH", f"/v1/transactions/{created.id}", json=update_payload
    )
    assert_response_matches_openapi(
        update_response, "updateTransaction", httpx.codes.OK, openapi_snapshot
    )
    updated = json_object_response(update_response, httpx.codes.OK)
    assert updated["description"] == update_payload["description"]
    assert updated["accountId"] == update_payload["accountId"]
    persisted = _fetch_transaction(gateway_client, created.id, openapi_snapshot)
    _assert_transaction(persisted, updated)
    for immutable in ("amount", "date", "type", "currencyIsoCode", "bankName"):
        assert persisted.get(immutable) == created.body.get(immutable)

    delete_response = gateway_client.api_request("DELETE", f"/v1/transactions/{created.id}")
    assert_response_matches_openapi(
        delete_response, "deleteTransaction", httpx.codes.NO_CONTENT, openapi_snapshot
    )
    assert_no_content(delete_response)
    missing = gateway_client.api_request("GET", f"/v1/transactions/{created.id}")
    assert_response_matches_openapi(
        missing, "getTransaction", httpx.codes.NOT_FOUND, openapi_snapshot
    )
    assert_api_error(missing, httpx.codes.NOT_FOUND, error_type="NOT_FOUND")


@pytest.mark.openapi("previewTransactions")
@pytest.mark.openapi("batchImportTransactions")
@pytest.mark.destructive
@pytest.mark.mutation
def test_ordered_preview_and_import_report_exact_values_and_duplicate_behavior(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
    transaction_statement_format_id: int,
) -> None:
    labels = ("import-base", "duplicate-same-file", "duplicate-skip", "duplicate-explicit")
    account_id = generated_account_id(run_state, "ordered-import")

    preview = preview_transaction_files(
        gateway_client,
        run_state,
        labels,
        statement_format_id=transaction_statement_format_id,
        account_id=account_id,
        openapi_snapshot=openapi_snapshot,
    )

    preview_files = array_field(preview, "files")
    assert len(preview_files) == len(labels)
    for index, label in enumerate(labels):
        preview_file = object_item(preview_files[index])
        assert preview_file["sourceFile"] == f"{label}.csv"
        assert int_field(preview_file, "statementFormatId") == transaction_statement_format_id
        string_field(preview_file, "previewImportToken")
        rows = array_field(preview_file, "transactions")
        assert len(rows) == 2
        assert [object_item(row)["description"] for row in rows] == [
            f"{run_state.run_id} {label} debit",
            f"{run_state.run_id} {label} credit",
        ]
        for row, date, amount, transaction_type in zip(
            rows,
            ("2026-01-15", "2026-01-16"),
            (12.34, 5.67),
            ("DEBIT", "CREDIT"),
            strict=True,
        ):
            transaction = object_item(row)
            assert transaction["date"] == date
            assert transaction["amount"] == amount
            assert transaction["type"] == transaction_type
            assert transaction["currencyIsoCode"] == "USD"
            assert transaction["accountId"] == account_id
            assert bool_field(transaction, "duplicate") is False

    preview_objects = [object_item(item) for item in preview_files]
    base_transactions = [
        import_transaction_request(run_state, "import-row-1", account_id=account_id),
        import_transaction_request(
            run_state,
            "import-row-2",
            account_id=account_id,
            amount=5.67,
            transaction_type="CREDIT",
        ),
    ]
    duplicate = import_transaction_request(
        run_state,
        "duplicate-controlled",
        account_id=account_id,
        allow_duplicate=False,
    )
    explicit = import_transaction_request(
        run_state,
        "duplicate-controlled",
        account_id=account_id,
        allow_duplicate=True,
    )
    import_response = gateway_client.api_request(
        "POST",
        "/v1/transactions/batch",
        json=batch_import_request(
            [
                batch_import_file_request(
                    preview_import_token=string_field(preview_objects[0], "previewImportToken"),
                    transactions=base_transactions,
                ),
                batch_import_file_request(
                    preview_import_token=string_field(preview_objects[1], "previewImportToken"),
                    transactions=[duplicate, duplicate],
                ),
                batch_import_file_request(
                    preview_import_token=string_field(preview_objects[2], "previewImportToken"),
                    transactions=[duplicate],
                ),
                batch_import_file_request(
                    preview_import_token=string_field(preview_objects[3], "previewImportToken"),
                    transactions=[explicit],
                ),
            ]
        ),
    )

    assert_response_matches_openapi(
        import_response, "batchImportTransactions", httpx.codes.OK, openapi_snapshot
    )
    result = json_object_response(import_response, httpx.codes.OK)
    assert (result["created"], result["duplicatesSkipped"], result["duplicatesImported"]) == (
        5,
        1,
        1,
    )
    files = [object_item(item) for item in array_field(result, "files")]
    assert [item["sourceFile"] for item in files] == [f"{label}.csv" for label in labels]
    assert [item["created"] for item in files] == [2, 2, 0, 1]
    assert [item["duplicatesSkipped"] for item in files] == [0, 0, 1, 0]
    assert [item["duplicatesImported"] for item in files] == [0, 0, 0, 1]
    assert [len(array_field(item, "transactions")) for item in files] == [2, 2, 0, 1]

    matching = [
        item
        for item in _fetch_transactions(gateway_client, openapi_snapshot)
        if item.get("accountId") == account_id
    ]
    assert len(matching) == 5
    assert sum(item.get("description") == duplicate["description"] for item in matching) == 3
    assert {
        (item["description"], item["amount"], item["type"])
        for item in matching
        if item.get("description") != duplicate["description"]
    } == {
        (base_transactions[0]["description"], 12.34, "DEBIT"),
        (base_transactions[1]["description"], 5.67, "CREDIT"),
    }


@pytest.mark.openapi("batchImportTransactions")
@pytest.mark.mutation
def test_invalid_later_import_token_does_not_partially_import_earlier_file(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
    transaction_statement_format_id: int,
) -> None:
    account_id = generated_account_id(run_state, "atomic-batch")
    preview = preview_transaction_files(
        gateway_client,
        run_state,
        ("atomic-valid",),
        statement_format_id=transaction_statement_format_id,
        account_id=account_id,
        openapi_snapshot=openapi_snapshot,
    )
    preview_file = object_item(array_field(preview, "files")[0])
    transaction = import_transaction_request(run_state, "atomic-row", account_id=account_id)
    response = gateway_client.api_request(
        "POST",
        "/v1/transactions/batch",
        json=batch_import_request(
            [
                batch_import_file_request(
                    preview_import_token=string_field(preview_file, "previewImportToken"),
                    transactions=[transaction],
                ),
                batch_import_file_request(
                    preview_import_token="invalid-preview-token",
                    transactions=[transaction],
                ),
            ]
        ),
    )

    assert_response_matches_openapi(
        response, "batchImportTransactions", httpx.codes.UNPROCESSABLE_ENTITY, openapi_snapshot
    )
    assert_api_error(
        response,
        httpx.codes.UNPROCESSABLE_ENTITY,
        error_type="APPLICATION_ERROR",
        code="PREVIEW_IMPORT_TOKEN_INVALID",
    )
    assert not [
        item
        for item in _fetch_transactions(gateway_client, openapi_snapshot)
        if item.get("accountId") == account_id
    ]


@pytest.mark.openapi("bulkDeleteTransactions")
@pytest.mark.destructive
@pytest.mark.mutation
def test_bulk_delete_transactions_deletes_created_transactions(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    first = create_manual_transaction(
        gateway_client, run_state, "bulk-delete-tx-1", openapi_snapshot=openapi_snapshot
    )
    second = create_manual_transaction(
        gateway_client, run_state, "bulk-delete-tx-2", openapi_snapshot=openapi_snapshot
    )
    transaction_ids = [first.id, second.id]

    response = gateway_client.api_request(
        "POST", "/v1/transactions/bulk-delete", json=bulk_delete_request(transaction_ids)
    )

    assert_response_matches_openapi(
        response, "bulkDeleteTransactions", httpx.codes.OK, openapi_snapshot
    )
    result = json_object_response(response, httpx.codes.OK)
    assert int_field(result, "deletedCount") == len(transaction_ids)
    assert array_field(result, "notFoundIds") == []
    remaining_ids = {
        int_field(item, "id") for item in _fetch_transactions(gateway_client, openapi_snapshot)
    }
    assert remaining_ids.isdisjoint(transaction_ids)


@pytest.mark.openapi("createTransaction")
@pytest.mark.mutation
@pytest.mark.parametrize(
    ("request_kwargs", "error_type"),
    [
        ({"json": {"date": "2026-01-15"}}, "VALIDATION_ERROR"),
        (
            {"content": b'{"date":', "headers": {"content-type": "application/json"}},
            "INVALID_REQUEST",
        ),
        (
            {
                "json": {
                    "date": "2026-01-15",
                    "description": "invalid enum",
                    "amount": 1,
                    "currencyIsoCode": "USD",
                    "type": "SIDEWAYS",
                }
            },
            "INVALID_REQUEST",
        ),
    ],
)
def test_create_transaction_rejects_invalid_json_binding(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
    request_kwargs: dict[str, object],
    error_type: str,
) -> None:
    response = gateway_client.api_request("POST", "/v1/transactions", **request_kwargs)

    assert_response_matches_openapi(
        response, "createTransaction", httpx.codes.BAD_REQUEST, openapi_snapshot
    )
    assert_api_error(response, httpx.codes.BAD_REQUEST, error_type=error_type)


@pytest.mark.openapi("createTransaction")
@pytest.mark.mutation
def test_create_transaction_rejects_invalid_currency_value(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/transactions",
        json={
            "date": "2026-01-15",
            "description": f"{run_state.run_id} invalid currency",
            "amount": 1,
            "currencyIsoCode": "ZZZ",
            "type": "DEBIT",
        },
    )

    assert_response_matches_openapi(
        response, "createTransaction", httpx.codes.UNPROCESSABLE_ENTITY, openapi_snapshot
    )
    assert_api_error(
        response,
        httpx.codes.UNPROCESSABLE_ENTITY,
        error_type="APPLICATION_ERROR",
        code="TRANSACTION_CURRENCY_INVALID",
    )


@pytest.mark.openapi("bulkDeleteTransactions")
@pytest.mark.destructive
@pytest.mark.mutation
def test_bulk_delete_rejects_schema_defined_invalid_empty_ids(
    gateway_client: GatewayClient,
) -> None:
    response = gateway_client.api_request("POST", "/v1/transactions/bulk-delete", json={"ids": []})

    # Only stable error fields are asserted: rejectedValue is diagnostic and its
    # concrete JSON type is not part of this regression scenario.
    assert_api_error(response, httpx.codes.BAD_REQUEST, error_type="VALIDATION_ERROR")


def _fetch_transactions(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> list[JsonObject]:
    response = gateway_client.api_request("GET", "/v1/transactions")
    assert_response_matches_openapi(response, "getTransactions", httpx.codes.OK, openapi_snapshot)
    return [object_item(item) for item in json_array_response(response, httpx.codes.OK)]


def _fetch_transaction(
    gateway_client: GatewayClient,
    transaction_id: int,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    response = gateway_client.api_request("GET", f"/v1/transactions/{transaction_id}")
    assert_response_matches_openapi(response, "getTransaction", httpx.codes.OK, openapi_snapshot)
    return json_object_response(response, httpx.codes.OK)


def _assert_transaction(actual: JsonObject, expected: JsonObject) -> None:
    for field in (
        "id",
        "date",
        "description",
        "amount",
        "type",
        "currencyIsoCode",
        "accountId",
        "bankName",
    ):
        assert actual.get(field) == expected.get(field)
