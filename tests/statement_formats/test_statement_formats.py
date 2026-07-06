from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import (
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
    create_statement_format_request,
    csv_preview_request,
    csv_sample_bytes,
    csv_save_request,
    multipart_json,
    pdf_preview_request,
    pdf_sample_bytes,
    pdf_save_request,
    update_statement_format_request,
)
from api_tests.client import GatewayClient
from api_tests.run_state import RunState


@pytest.mark.openapi("listFormats")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_list_formats_returns_visible_formats(gateway_client: GatewayClient) -> None:
    response = gateway_client.api_request(
        "GET",
        "/v1/statement-formats",
        params={"includeHidden": False},
    )

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
) -> None:
    statement_format = _create_statement_format(gateway_client, run_state, "create-format")

    assert statement_format["formatType"] == "CSV"
    assert statement_format["defaultCurrencyIsoCode"] == "USD"
    assert string_field(statement_format, "displayName").startswith(run_state.run_id)


@pytest.mark.openapi("getFormat")
@pytest.mark.mutation
def test_get_format_returns_created_format(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = _create_statement_format(gateway_client, run_state, "get-format")
    statement_format_id = int_field(created, "id")

    response = gateway_client.api_request("GET", f"/v1/statement-formats/{statement_format_id}")

    statement_format = json_object_response(response, httpx.codes.OK)
    assert int_field(statement_format, "id") == statement_format_id
    assert statement_format["displayName"] == created["displayName"]


@pytest.mark.openapi("updateFormat")
@pytest.mark.mutation
def test_update_format_updates_created_format(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = _create_statement_format(gateway_client, run_state, "update-format")
    statement_format_id = int_field(created, "id")
    update_payload = update_statement_format_request(run_state, "update-format")

    response = gateway_client.api_request(
        "PUT",
        f"/v1/statement-formats/{statement_format_id}",
        json=update_payload,
    )

    updated = json_object_response(response, httpx.codes.OK)
    assert int_field(updated, "id") == statement_format_id
    assert updated["displayName"] == update_payload["displayName"]
    assert updated["bankName"] == update_payload["bankName"]


@pytest.mark.openapi("hideFormat")
@pytest.mark.destructive
@pytest.mark.mutation
def test_hide_format_hides_format_for_current_user(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = _create_statement_format(gateway_client, run_state, "hide-format")
    statement_format_id = int_field(created, "id")

    response = gateway_client.api_request(
        "POST", f"/v1/statement-formats/{statement_format_id}/hide"
    )

    assert_no_content(response)


@pytest.mark.openapi("unhideFormat")
@pytest.mark.destructive
@pytest.mark.mutation
def test_unhide_format_restores_format_for_current_user(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    created = _create_statement_format(gateway_client, run_state, "unhide-format")
    statement_format_id = int_field(created, "id")
    assert_no_content(
        gateway_client.api_request("POST", f"/v1/statement-formats/{statement_format_id}/hide")
    )

    response = gateway_client.api_request(
        "POST",
        f"/v1/statement-formats/{statement_format_id}/unhide",
    )

    assert_no_content(response)


@pytest.mark.openapi("analyzeCsvSample")
@pytest.mark.mutation
def test_analyze_csv_sample_returns_inferred_mapping(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/csv-wizard/analyze",
        files={"file": ("analysis.csv", csv_sample_bytes(run_state, "analysis"), "text/csv")},
    )

    analysis = json_object_response(response, httpx.codes.OK)
    assert array_field(analysis, "headers")
    assert array_field(analysis, "sampleRows")
    object_item(analysis["inferredMapping"])


@pytest.mark.openapi("previewCsvMapping")
@pytest.mark.mutation
def test_preview_csv_mapping_returns_preview_transactions(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/csv-wizard/preview",
        files={
            "file": ("preview.csv", csv_sample_bytes(run_state, "preview"), "text/csv"),
            "request": multipart_json(csv_preview_request(run_state, "preview")),
        },
    )

    preview = json_object_response(response, httpx.codes.OK)
    assert array_field(preview, "transactions")


@pytest.mark.openapi("saveCsvWizardFormat")
@pytest.mark.mutation
def test_save_csv_wizard_format_creates_statement_format(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/csv-wizard/save",
        files={
            "file": ("save.csv", csv_sample_bytes(run_state, "save"), "text/csv"),
            "request": multipart_json(csv_save_request(run_state, "save")),
        },
    )

    statement_format = json_object_response(response, httpx.codes.CREATED)
    int_field(statement_format, "id")
    assert statement_format["formatType"] == "CSV"
    assert statement_format["defaultCurrencyIsoCode"] == "USD"


@pytest.mark.openapi("analyzePdfSample")
@pytest.mark.mutation
def test_analyze_pdf_sample_returns_analysis_or_documented_parse_error(
    gateway_client: GatewayClient,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/pdf-wizard/analyze",
        files={"file": ("analysis.pdf", pdf_sample_bytes(), "application/pdf")},
    )

    _assert_pdf_wizard_response(response, success_status=httpx.codes.OK)


@pytest.mark.openapi("previewPdfMapping")
@pytest.mark.mutation
def test_preview_pdf_mapping_returns_preview_or_documented_parse_error(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/pdf-wizard/preview",
        files={
            "file": ("preview.pdf", pdf_sample_bytes(), "application/pdf"),
            "request": multipart_json(pdf_preview_request(run_state, "preview-pdf")),
        },
    )

    _assert_pdf_wizard_response(response, success_status=httpx.codes.OK)


@pytest.mark.openapi("savePdfWizardFormat")
@pytest.mark.mutation
def test_save_pdf_wizard_format_creates_format_or_returns_documented_parse_error(
    gateway_client: GatewayClient,
    run_state: RunState,
) -> None:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats/pdf-wizard/save",
        files={
            "file": ("save.pdf", pdf_sample_bytes(), "application/pdf"),
            "request": multipart_json(pdf_save_request(run_state, "save-pdf")),
        },
    )

    _assert_pdf_wizard_response(response, success_status=httpx.codes.CREATED)


def _create_statement_format(
    gateway_client: GatewayClient,
    run_state: RunState,
    label: str,
) -> JsonObject:
    response = gateway_client.api_request(
        "POST",
        "/v1/statement-formats",
        json=create_statement_format_request(run_state, label),
    )
    return json_object_response(response, httpx.codes.CREATED)


def _assert_pdf_wizard_response(response: httpx.Response, *, success_status: int) -> None:
    assert_status_in(
        response,
        {success_status, httpx.codes.BAD_REQUEST, httpx.codes.UNPROCESSABLE_ENTITY},
    )
    if response.status_code in {httpx.codes.BAD_REQUEST, httpx.codes.UNPROCESSABLE_ENTITY}:
        error = json_object_response(response, response.status_code)
        string_field(error, "type")
        string_field(error, "message")
        return

    body = json_object_response(response, success_status)
    if success_status == httpx.codes.CREATED:
        int_field(body, "id")
