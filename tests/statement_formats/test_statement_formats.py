from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import (
    JsonArray,
    JsonObject,
    array_field,
    assert_no_content,
    assert_status_in,
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
from api_tests.openapi import OpenApiDocument
from api_tests.run_state import RunState
from api_tests.schemas import assert_response_matches_openapi


@pytest.mark.openapi("listFormats")
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
        int_field(statement_format, "id")
        string_field(statement_format, "createdAt")


@pytest.mark.openapi("createFormat")
@pytest.mark.mutation
def test_create_format_creates_user_csv_format(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    statement_format = _create_statement_format(
        gateway_client,
        run_state,
        "create-format",
        openapi_snapshot,
    )

    assert statement_format["formatType"] == "CSV"
    assert statement_format["defaultCurrencyIsoCode"] == "USD"
    assert string_field(statement_format, "displayName").startswith(run_state.run_id)


@pytest.mark.openapi("getFormat")
@pytest.mark.mutation
def test_get_format_returns_created_format(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = _create_statement_format(gateway_client, run_state, "get-format", openapi_snapshot)
    statement_format_id = int_field(created, "id")

    response = gateway_client.api_request("GET", f"/v1/statement-formats/{statement_format_id}")

    assert_response_matches_openapi(response, "getFormat", httpx.codes.OK, openapi_snapshot)
    statement_format = json_object_response(response, httpx.codes.OK)
    assert int_field(statement_format, "id") == statement_format_id
    assert statement_format["displayName"] == created["displayName"]


@pytest.mark.openapi("updateFormat")
@pytest.mark.mutation
def test_update_format_updates_created_format(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = _create_statement_format(
        gateway_client,
        run_state,
        "update-format",
        openapi_snapshot,
    )
    statement_format_id = int_field(created, "id")
    update_payload = update_statement_format_request(run_state, "update-format")

    response = gateway_client.api_request(
        "PUT",
        f"/v1/statement-formats/{statement_format_id}",
        json=update_payload,
    )

    assert_response_matches_openapi(response, "updateFormat", httpx.codes.OK, openapi_snapshot)
    updated = json_object_response(response, httpx.codes.OK)
    assert int_field(updated, "id") == statement_format_id
    assert updated["displayName"] == update_payload["displayName"]
    assert updated["bankName"] == update_payload["bankName"]

    get_response = gateway_client.api_request("GET", f"/v1/statement-formats/{statement_format_id}")
    assert_response_matches_openapi(get_response, "getFormat", httpx.codes.OK, openapi_snapshot)
    fetched = json_object_response(get_response, httpx.codes.OK)
    assert int_field(fetched, "id") == statement_format_id
    assert fetched["displayName"] == update_payload["displayName"]
    assert fetched["bankName"] == update_payload["bankName"]
    assert fetched["defaultCurrencyIsoCode"] == update_payload["defaultCurrencyIsoCode"]


@pytest.mark.openapi("hideFormat")
@pytest.mark.destructive
@pytest.mark.mutation
def test_hide_format_hides_format_for_current_user(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = _create_statement_format(gateway_client, run_state, "hide-format", openapi_snapshot)
    statement_format_id = int_field(created, "id")

    response = gateway_client.api_request(
        "POST", f"/v1/statement-formats/{statement_format_id}/hide"
    )

    assert_response_matches_openapi(
        response, "hideFormat", httpx.codes.NO_CONTENT, openapi_snapshot
    )
    assert_no_content(response)

    list_response = gateway_client.api_request(
        "GET",
        "/v1/statement-formats",
        params={"includeHidden": False},
    )
    assert_response_matches_openapi(list_response, "listFormats", httpx.codes.OK, openapi_snapshot)
    formats = json_array_response(list_response, httpx.codes.OK)
    _assert_statement_format_absent(formats, statement_format_id)


@pytest.mark.openapi("unhideFormat")
@pytest.mark.destructive
@pytest.mark.mutation
def test_unhide_format_restores_format_for_current_user(
    gateway_client: GatewayClient,
    run_state: RunState,
    openapi_snapshot: OpenApiDocument,
) -> None:
    created = _create_statement_format(
        gateway_client,
        run_state,
        "unhide-format",
        openapi_snapshot,
    )
    statement_format_id = int_field(created, "id")
    hide_response = gateway_client.api_request(
        "POST",
        f"/v1/statement-formats/{statement_format_id}/hide",
    )
    assert_response_matches_openapi(
        hide_response,
        "hideFormat",
        httpx.codes.NO_CONTENT,
        openapi_snapshot,
    )
    assert_no_content(hide_response)
    hidden_list_response = gateway_client.api_request(
        "GET",
        "/v1/statement-formats",
        params={"includeHidden": False},
    )
    assert_response_matches_openapi(
        hidden_list_response,
        "listFormats",
        httpx.codes.OK,
        openapi_snapshot,
    )
    _assert_statement_format_absent(
        json_array_response(hidden_list_response, httpx.codes.OK),
        statement_format_id,
    )

    response = gateway_client.api_request(
        "POST",
        f"/v1/statement-formats/{statement_format_id}/unhide",
    )

    assert_response_matches_openapi(
        response,
        "unhideFormat",
        httpx.codes.NO_CONTENT,
        openapi_snapshot,
    )
    assert_no_content(response)

    visible_list_response = gateway_client.api_request(
        "GET",
        "/v1/statement-formats",
        params={"includeHidden": False},
    )
    assert_response_matches_openapi(
        visible_list_response,
        "listFormats",
        httpx.codes.OK,
        openapi_snapshot,
    )
    restored = _find_statement_format(
        json_array_response(visible_list_response, httpx.codes.OK),
        statement_format_id,
    )
    assert restored["displayName"] == created["displayName"]


@pytest.mark.openapi("getFormat")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_format_unknown_id_returns_not_found(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request("GET", "/v1/statement-formats/9223372036854775807")

    assert_response_matches_openapi(response, "getFormat", httpx.codes.NOT_FOUND, openapi_snapshot)
    json_object_response(response, httpx.codes.NOT_FOUND)


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
    assert array_field(analysis, "headers")
    assert array_field(analysis, "sampleRows")
    object_item(analysis["inferredMapping"])


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
    assert array_field(preview, "transactions")


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
    assert headers or sample_rows
    if headers:
        header_names = {header for header in headers if isinstance(header, str)}
        assert {"Date", "Description", "Amount"}.issubset(header_names)


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
    transactions = array_field(preview, "transactions")
    assert transactions
    transaction = object_item(transactions[0])
    string_field(transaction, "date")
    string_field(transaction, "description")


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
    statement_format_id = int_field(statement_format, "id")
    assert statement_format["formatType"] == "PDF"
    assert statement_format["defaultCurrencyIsoCode"] == "USD"
    if "scope" in statement_format:
        assert statement_format["scope"] == "USER"
    if "ownerId" in statement_format:
        string_field(statement_format, "ownerId")

    list_response = gateway_client.api_request(
        "GET",
        "/v1/statement-formats",
        params={"includeHidden": False},
    )
    assert_response_matches_openapi(list_response, "listFormats", httpx.codes.OK, openapi_snapshot)
    formats = json_array_response(list_response, httpx.codes.OK)
    visible_format = _find_statement_format(formats, statement_format_id)
    assert visible_format["formatType"] == "PDF"
    assert visible_format["displayName"] == statement_format["displayName"]


@pytest.mark.openapi("analyzePdfSample")
@pytest.mark.mutation
def test_analyze_pdf_sample_rejects_invalid_pdf(gateway_client: GatewayClient) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/pdf-wizard/analyze",
        files={"file": ("invalid.pdf", b"not a pdf", "application/pdf")},
    )

    _assert_pdf_wizard_error(response)


@pytest.mark.openapi("previewPdfMapping")
@pytest.mark.mutation
def test_preview_pdf_mapping_rejects_invalid_pdf(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/pdf-wizard/preview",
        files={
            "file": ("invalid.pdf", b"not a pdf", "application/pdf"),
            "request": multipart_json(pdf_preview_request(run_state, "invalid-preview-pdf")),
        },
    )

    _assert_pdf_wizard_error(response)


@pytest.mark.openapi("savePdfWizardFormat")
@pytest.mark.mutation
def test_save_pdf_wizard_format_rejects_invalid_pdf(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/pdf-wizard/save",
        files={
            "file": ("invalid.pdf", b"not a pdf", "application/pdf"),
            "request": multipart_json(pdf_save_request(run_state, "invalid-save-pdf")),
        },
    )

    _assert_pdf_wizard_error(response)


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


def _assert_pdf_wizard_error(response: httpx.Response) -> None:
    assert_status_in(
        response,
        {httpx.codes.BAD_REQUEST, httpx.codes.UNPROCESSABLE_ENTITY},
    )
    error = json_object_response(response, response.status_code)
    string_field(error, "type")
    string_field(error, "message")


def _find_statement_format(formats: JsonArray, statement_format_id: int) -> JsonObject:
    for item in formats:
        statement_format = object_item(item)
        if statement_format.get("id") == statement_format_id:
            return statement_format
    raise AssertionError(f"expected statement format {statement_format_id} in list response")


def _assert_statement_format_absent(formats: JsonArray, statement_format_id: int) -> None:
    for item in formats:
        statement_format = object_item(item)
        if statement_format.get("id") == statement_format_id:
            raise AssertionError(
                f"expected statement format {statement_format_id} to be absent from list response"
            )
