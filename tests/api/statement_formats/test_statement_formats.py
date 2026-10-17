from __future__ import annotations

from collections.abc import Sequence

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
from api_tests.builders.statement_formats import (
    basic_csv_sample_bytes,
    create_statement_format_request,
    csv_preview_request,
    csv_save_request,
    multipart_json,
    pdf_preview_request,
    pdf_sample_bytes,
    pdf_save_request,
    update_statement_format_request,
)
from api_tests.client import GatewayClient
from api_tests.openapi import JsonValue, OpenApiDocument
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi


@pytest.mark.openapi("listFormats")
@pytest.mark.openapi("getFormat")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_list_formats_returns_visible_formats(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "GET",
        "/v1/statement-formats",
        params={"includeHidden": False},
    )

    assert_response_matches_openapi(response, "listFormats", httpx.codes.OK, openapi_snapshot)
    formats = json_array_response(response, httpx.codes.OK)
    for item in formats:
        statement_format = object_item(item)
        statement_format_id = int_field(statement_format, "id")
        string_field(statement_format, "createdAt")
        if statement_format.get("scope") == "SYSTEM":
            persisted = _get_statement_format(
                gateway_client,
                statement_format_id,
                openapi_snapshot,
            )
            assert persisted.get("scope") == "SYSTEM"


@pytest.mark.openapi("createFormat")
@pytest.mark.openapi("getFormat")
@pytest.mark.openapi("updateFormat")
@pytest.mark.openapi("hideFormat")
@pytest.mark.openapi("unhideFormat")
@pytest.mark.destructive
@pytest.mark.mutation
def test_statement_format_lifecycle_persists_update_hide_and_unhide(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = _create_statement_format(
        gateway_client,
        run_state,
        "format-lifecycle",
        openapi_snapshot,
    )
    statement_format_id = int_field(created, "id")
    assert created["formatType"] == "CSV"
    assert created["defaultCurrencyIsoCode"] == "USD"
    assert string_field(created, "displayName").startswith(run_state.run_id)

    update_payload = update_statement_format_request(run_state, "format-lifecycle")

    update_response = gateway_client.api_request(
        "PUT",
        f"/v1/statement-formats/{statement_format_id}",
        json=update_payload,
    )
    assert_response_matches_openapi(
        update_response, "updateFormat", httpx.codes.OK, openapi_snapshot
    )
    updated = json_object_response(update_response, httpx.codes.OK)
    assert int_field(updated, "id") == statement_format_id
    assert updated["displayName"] == update_payload["displayName"]
    assert updated["bankName"] == update_payload["bankName"]
    assert updated["defaultCurrencyIsoCode"] == update_payload["defaultCurrencyIsoCode"]
    persisted = _get_statement_format(gateway_client, statement_format_id, openapi_snapshot)
    assert persisted["displayName"] == update_payload["displayName"]
    assert persisted["bankName"] == update_payload["bankName"]
    assert persisted["formatType"] == created["formatType"]

    hide_response = gateway_client.api_request(
        "POST", f"/v1/statement-formats/{statement_format_id}/hide"
    )
    assert_response_matches_openapi(
        hide_response, "hideFormat", httpx.codes.NO_CONTENT, openapi_snapshot
    )
    assert_no_content(hide_response)
    visible = _list_statement_formats(gateway_client, False, openapi_snapshot)
    assert not any(item.get("id") == statement_format_id for item in visible)
    hidden = _find_statement_format(
        _list_statement_formats(gateway_client, True, openapi_snapshot),
        statement_format_id,
    )
    assert hidden["hidden"] is True

    unhide_response = gateway_client.api_request(
        "POST",
        f"/v1/statement-formats/{statement_format_id}/unhide",
    )
    assert_response_matches_openapi(
        unhide_response,
        "unhideFormat",
        httpx.codes.NO_CONTENT,
        openapi_snapshot,
    )
    assert_no_content(unhide_response)
    restored = _find_statement_format(
        _list_statement_formats(gateway_client, False, openapi_snapshot),
        statement_format_id,
    )
    assert restored["displayName"] == update_payload["displayName"]
    assert restored["hidden"] is False


@pytest.mark.openapi("analyzeCsvSample")
@pytest.mark.mutation
def test_analyze_csv_sample_returns_inferred_mapping(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/csv-wizard/analyze",
        files={"file": ("analysis.csv", basic_csv_sample_bytes(), "text/csv")},
    )

    assert_response_matches_openapi(response, "analyzeCsvSample", httpx.codes.OK, openapi_snapshot)
    analysis = json_object_response(response, httpx.codes.OK)
    assert array_field(analysis, "headers") == [
        "Date",
        "Description",
        "Amount",
        "Type",
        "Category",
    ]
    assert array_field(analysis, "sampleRows") == [
        {
            "Date": "2026-01-15",
            "Description": "Coffee Shop",
            "Amount": "12.34",
            "Type": "DEBIT",
            "Category": "API Test",
        },
        {
            "Date": "2026-01-16",
            "Description": "Payroll",
            "Amount": "5.67",
            "Type": "CREDIT",
            "Category": "API Test",
        },
    ]
    mapping = object_item(analysis["inferredMapping"])
    assert mapping["dateColumn"] == "Date"
    assert mapping["descriptionColumn"] == "Description"
    assert mapping["amountColumn"] == "Amount"
    assert mapping["typeColumn"] == "Type"


@pytest.mark.openapi("previewCsvMapping")
@pytest.mark.mutation
def test_preview_csv_mapping_returns_preview_transactions(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/csv-wizard/preview",
        files={
            "file": ("preview.csv", basic_csv_sample_bytes(), "text/csv"),
            "request": multipart_json(csv_preview_request(run_state, "preview")),
        },
    )

    assert_response_matches_openapi(response, "previewCsvMapping", httpx.codes.OK, openapi_snapshot)
    preview = json_object_response(response, httpx.codes.OK)
    transactions = [object_item(item) for item in array_field(preview, "transactions")]
    _assert_wizard_transactions(transactions, bank_name=f"{run_state.run_id} preview Bank")


@pytest.mark.openapi("saveCsvWizardFormat")
@pytest.mark.mutation
def test_save_csv_wizard_format_creates_statement_format(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/csv-wizard/save",
        files={
            "file": ("save.csv", basic_csv_sample_bytes(), "text/csv"),
            "request": multipart_json(csv_save_request(run_state, "save")),
        },
    )

    assert_response_matches_openapi(
        response,
        "saveCsvWizardFormat",
        httpx.codes.CREATED,
        openapi_snapshot,
    )
    statement_format = json_object_response(response, httpx.codes.CREATED)
    int_field(statement_format, "id")
    assert statement_format["formatType"] == "CSV"
    assert statement_format["defaultCurrencyIsoCode"] == "USD"
    statement_format_id = int_field(statement_format, "id")
    persisted = _get_statement_format(gateway_client, statement_format_id, openapi_snapshot)
    assert persisted["displayName"] == f"{run_state.run_id} save Wizard CSV"
    assert persisted["bankName"] == f"{run_state.run_id} save Bank"


@pytest.mark.openapi("analyzePdfSample")
@pytest.mark.mutation
def test_analyze_pdf_sample_returns_table_candidates(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/pdf-wizard/analyze",
        files={"file": ("analysis.pdf", pdf_sample_bytes(), "application/pdf")},
    )

    assert_response_matches_openapi(response, "analyzePdfSample", httpx.codes.OK, openapi_snapshot)
    analysis = json_object_response(response, httpx.codes.OK)
    candidates = array_field(analysis, "candidates")
    assert candidates
    candidate = object_item(candidates[0])
    headers = array_field(candidate, "headers")
    sample_rows = array_field(candidate, "sampleRows")
    assert headers == ["Date", "Description", "Amount"]
    assert sample_rows == [
        ["2026-01-15", "Coffee Shop", "-12.34"],
        ["2026-01-16", "Payroll", "5.67"],
    ]
    assert int_field(candidate, "rowCount") == 2


@pytest.mark.openapi("previewPdfMapping")
@pytest.mark.mutation
def test_preview_pdf_mapping_returns_preview_transactions(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/pdf-wizard/preview",
        files={
            "file": ("preview.pdf", pdf_sample_bytes(), "application/pdf"),
            "request": multipart_json(pdf_preview_request(run_state, "preview-pdf")),
        },
    )

    assert_response_matches_openapi(response, "previewPdfMapping", httpx.codes.OK, openapi_snapshot)
    preview = json_object_response(response, httpx.codes.OK)
    transactions = [object_item(item) for item in array_field(preview, "transactions")]
    _assert_wizard_transactions(
        transactions,
        bank_name=f"{run_state.run_id} preview-pdf Bank",
        expect_category=False,
    )


@pytest.mark.openapi("savePdfWizardFormat")
@pytest.mark.mutation
def test_save_pdf_wizard_format_creates_visible_user_pdf_format(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/pdf-wizard/save",
        files={
            "file": ("save.pdf", pdf_sample_bytes(), "application/pdf"),
            "request": multipart_json(pdf_save_request(run_state, "save-pdf")),
        },
    )

    assert_response_matches_openapi(
        response,
        "savePdfWizardFormat",
        httpx.codes.CREATED,
        openapi_snapshot,
    )
    statement_format = json_object_response(response, httpx.codes.CREATED)
    int_field(statement_format, "id")
    assert statement_format["formatType"] == "PDF"
    assert statement_format["defaultCurrencyIsoCode"] == "USD"
    if "scope" in statement_format:
        assert statement_format["scope"] == "USER"
    if "ownerId" in statement_format:
        string_field(statement_format, "ownerId")
    statement_format_id = int_field(statement_format, "id")
    persisted = _get_statement_format(gateway_client, statement_format_id, openapi_snapshot)
    assert persisted["displayName"] == f"{run_state.run_id} save-pdf Wizard PDF"
    assert persisted["formatType"] == "PDF"


@pytest.mark.openapi("previewCsvMapping")
@pytest.mark.mutation
def test_csv_preview_rejects_missing_multipart_request(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/csv-wizard/preview",
        files={"file": ("missing-request.csv", basic_csv_sample_bytes(), "text/csv")},
    )

    assert_response_matches_openapi(
        response, "previewCsvMapping", httpx.codes.BAD_REQUEST, openapi_snapshot
    )
    assert_api_error(response, httpx.codes.BAD_REQUEST, error_type="INVALID_REQUEST")


@pytest.mark.openapi("saveCsvWizardFormat")
@pytest.mark.mutation
def test_csv_wizard_save_rejects_invalid_file(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/csv-wizard/save",
        files={
            "file": ("invalid.csv", b"not,the,configured,headers\n", "text/csv"),
            "request": multipart_json(csv_save_request(run_state, "invalid-file")),
        },
    )

    assert_api_error(
        response,
        httpx.codes.UNPROCESSABLE_ENTITY,
        error_type="APPLICATION_ERROR",
        code="CSV_WIZARD_VALIDATION_FAILED",
    )


def _create_statement_format(
    gateway_client: GatewayClient,
    run_state: RunState,
    label: str,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats",
        json=create_statement_format_request(run_state, label),
    )
    assert_response_matches_openapi(response, "createFormat", httpx.codes.CREATED, openapi_snapshot)
    return json_object_response(response, httpx.codes.CREATED)


def _find_statement_format(formats: Sequence[JsonValue], statement_format_id: int) -> JsonObject:
    for item in formats:
        statement_format = object_item(item)
        if statement_format.get("id") == statement_format_id:
            return statement_format
    raise AssertionError(f"expected statement format {statement_format_id} in list response")


def _get_statement_format(
    gateway_client: GatewayClient,
    statement_format_id: int,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    response = gateway_client.api_request("GET", f"/v1/statement-formats/{statement_format_id}")
    assert_response_matches_openapi(response, "getFormat", httpx.codes.OK, openapi_snapshot)
    return json_object_response(response, httpx.codes.OK)


def _list_statement_formats(
    gateway_client: GatewayClient,
    include_hidden: bool,
    openapi_snapshot: OpenApiDocument,
) -> list[JsonObject]:
    response = gateway_client.api_request(
        "GET", "/v1/statement-formats", params={"includeHidden": include_hidden}
    )
    assert_response_matches_openapi(response, "listFormats", httpx.codes.OK, openapi_snapshot)
    return [object_item(item) for item in json_array_response(response, httpx.codes.OK)]


def _assert_wizard_transactions(
    transactions: list[JsonObject],
    *,
    bank_name: str,
    expect_category: bool = True,
) -> None:
    assert len(transactions) == 2
    for transaction, date, description, amount, transaction_type in zip(
        transactions,
        ("2026-01-15", "2026-01-16"),
        ("Coffee Shop", "Payroll"),
        (12.34, 5.67),
        ("DEBIT", "CREDIT"),
        strict=True,
    ):
        assert transaction["date"] == date
        assert transaction["description"] == description
        assert transaction["amount"] == amount
        assert transaction["type"] == transaction_type
        assert transaction["currencyIsoCode"] == "USD"
        assert transaction["bankName"] == bank_name
        assert transaction["duplicate"] is False
        if expect_category:
            assert transaction["category"] == "API Test"
