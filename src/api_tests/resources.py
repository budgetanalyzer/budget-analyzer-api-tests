from __future__ import annotations

from dataclasses import dataclass

import httpx

from api_tests.assertions import JsonObject, int_field, json_object_response, string_field
from api_tests.builders.statement_formats import (
    csv_sample_bytes,
    csv_save_request,
    multipart_json,
)
from api_tests.builders.transactions import (
    batch_import_request,
    generated_account_id,
    import_transaction_request,
)
from api_tests.builders.views import create_saved_view_request
from api_tests.client import GatewayClient
from api_tests.run_state import RunState


@dataclass(frozen=True, slots=True)
class CreatedStatementFormat:
    id: int
    body: JsonObject


@dataclass(frozen=True, slots=True)
class CreatedTransaction:
    id: int
    account_id: str
    body: JsonObject


@dataclass(frozen=True, slots=True)
class CreatedSavedView:
    id: str
    body: JsonObject


def save_csv_wizard_statement_format(
    client: GatewayClient,
    run_state: RunState,
    label: str,
) -> CreatedStatementFormat:
    response = client.api_request(
        "POST",
        "/v1/statement-formats/csv-wizard/save",
        files={
            "file": (f"{label}.csv", csv_sample_bytes(run_state, label), "text/csv"),
            "request": multipart_json(csv_save_request(run_state, label)),
        },
    )
    body = json_object_response(response, httpx.codes.CREATED)
    return CreatedStatementFormat(id=int_field(body, "id"), body=body)


def preview_transactions(
    client: GatewayClient,
    run_state: RunState,
    label: str,
    *,
    statement_format_id: int,
    account_id: str,
) -> JsonObject:
    response = client.api_request(
        "POST",
        "/v1/transactions/preview",
        params={
            "statementFormatId": statement_format_id,
            "accountId": account_id,
        },
        files={
            "file": (f"{label}.csv", csv_sample_bytes(run_state, label), "text/csv"),
        },
    )
    return json_object_response(response, httpx.codes.OK)


def import_generated_transaction(
    client: GatewayClient,
    run_state: RunState,
    label: str,
) -> CreatedTransaction:
    statement_format = save_csv_wizard_statement_format(client, run_state, f"{label}-format")
    account_id = generated_account_id(run_state, label)
    preview = preview_transactions(
        client,
        run_state,
        label,
        statement_format_id=statement_format.id,
        account_id=account_id,
    )
    preview_import_token = string_field(preview, "previewImportToken")
    transaction_request = import_transaction_request(run_state, label, account_id=account_id)

    response = client.api_request(
        "POST",
        "/v1/transactions/batch",
        json=batch_import_request(
            preview_import_token=preview_import_token,
            transactions=[transaction_request],
        ),
    )
    body = json_object_response(response, httpx.codes.OK)
    transactions = body.get("transactions")
    if not isinstance(transactions, list) or not transactions:
        raise AssertionError(f"expected imported transactions in response, got: {body!r}")
    first_transaction = transactions[0]
    if not isinstance(first_transaction, dict):
        raise AssertionError(f"expected transaction object, got: {first_transaction!r}")
    transaction_body = {
        key: value for key, value in first_transaction.items() if isinstance(key, str)
    }
    return CreatedTransaction(
        id=int_field(transaction_body, "id"),
        account_id=account_id,
        body=transaction_body,
    )


def create_saved_view(
    client: GatewayClient,
    run_state: RunState,
    label: str,
    *,
    account_id: str | None = None,
) -> CreatedSavedView:
    response = client.api_request(
        "POST",
        "/v1/views",
        json=create_saved_view_request(run_state, label, account_id=account_id),
    )
    body = json_object_response(response, httpx.codes.CREATED)
    return CreatedSavedView(id=string_field(body, "id"), body=body)
