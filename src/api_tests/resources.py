from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import httpx

from api_tests.assertions import JsonObject, int_field, json_object_response, string_field
from api_tests.builders.statement_formats import (
    csv_sample_bytes,
    csv_save_request,
    multipart_json,
)
from api_tests.builders.transactions import (
    generated_account_id,
    generated_bank_name,
    manual_transaction_request,
)
from api_tests.builders.views import create_saved_view_request
from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi


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
    *,
    openapi_snapshot: OpenApiDocument | None = None,
) -> CreatedStatementFormat:
    response = client.api_request(
        "POST",
        "/v1/statement-formats/csv-wizard/save",
        files={
            "file": (f"{label}.csv", csv_sample_bytes(run_state, label), "text/csv"),
            "request": multipart_json(csv_save_request(run_state, label)),
        },
    )
    _assert_response_matches_snapshot(
        response,
        "saveCsvWizardFormat",
        httpx.codes.CREATED,
        openapi_snapshot,
    )
    body = json_object_response(response, httpx.codes.CREATED)
    return CreatedStatementFormat(id=int_field(body, "id"), body=body)


def preview_transaction_files(
    client: GatewayClient,
    run_state: RunState,
    labels: Sequence[str],
    *,
    statement_format_id: int,
    account_id: str,
    openapi_snapshot: OpenApiDocument | None = None,
) -> JsonObject:
    response = client.api_request(
        "POST",
        "/v1/transactions/preview",
        params={
            "statementFormatId": statement_format_id,
            "accountId": account_id,
        },
        files=[
            (
                "files",
                (f"{label}.csv", csv_sample_bytes(run_state, label), "text/csv"),
            )
            for label in labels
        ],
    )
    _assert_response_matches_snapshot(
        response,
        "previewTransactions",
        httpx.codes.OK,
        openapi_snapshot,
    )
    return json_object_response(response, httpx.codes.OK)


def create_manual_transaction(
    client: GatewayClient,
    run_state: RunState,
    label: str,
    *,
    account_id: str | None = None,
    include_bank_name: bool = True,
    amount: float = 12.34,
    transaction_type: Literal["CREDIT", "DEBIT"] = "DEBIT",
    date: str = "2026-01-15",
    openapi_snapshot: OpenApiDocument | None = None,
) -> CreatedTransaction:
    transaction_account_id = account_id or generated_account_id(run_state, label)
    response = client.api_request(
        "POST",
        "/v1/transactions",
        json=manual_transaction_request(
            run_state,
            label,
            account_id=transaction_account_id,
            bank_name=generated_bank_name(run_state, label) if include_bank_name else None,
            amount=amount,
            transaction_type=transaction_type,
            date=date,
        ),
    )
    _assert_response_matches_snapshot(
        response,
        "createTransaction",
        httpx.codes.CREATED,
        openapi_snapshot,
    )
    body = json_object_response(response, httpx.codes.CREATED)
    return CreatedTransaction(
        id=int_field(body, "id"),
        account_id=transaction_account_id,
        body=body,
    )


def create_saved_view(
    client: GatewayClient,
    run_state: RunState,
    label: str,
    *,
    transaction_ids: Sequence[int] = (),
    openapi_snapshot: OpenApiDocument | None = None,
) -> CreatedSavedView:
    response = client.api_request(
        "POST",
        "/v1/views",
        json=create_saved_view_request(run_state, label, transaction_ids=transaction_ids),
    )
    _assert_response_matches_snapshot(response, "createView", httpx.codes.CREATED, openapi_snapshot)
    body = json_object_response(response, httpx.codes.CREATED)
    return CreatedSavedView(id=string_field(body, "id"), body=body)


def _assert_response_matches_snapshot(
    response: httpx.Response,
    operation_id: str,
    expected_status: int,
    openapi_snapshot: OpenApiDocument | None,
) -> None:
    if openapi_snapshot is None:
        return
    assert_response_matches_openapi(response, operation_id, expected_status, openapi_snapshot)
