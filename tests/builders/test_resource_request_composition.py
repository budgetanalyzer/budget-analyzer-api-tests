from __future__ import annotations

import json
from collections.abc import Callable
from email import policy
from email.message import MIMEPart
from email.parser import BytesParser

import httpx

from api_tests.builders.statement_formats import csv_save_request
from api_tests.builders.transactions import (
    batch_import_request,
    generated_account_id,
    import_transaction_request,
)
from api_tests.builders.views import create_saved_view_request, update_saved_view_request
from api_tests.client import GatewayClient
from api_tests.config import load_environment
from api_tests.resources import (
    create_saved_view,
    import_generated_transaction,
    preview_transactions,
    save_csv_wizard_statement_format,
)
from api_tests.run_state import RunState
from api_tests.session import SessionBundle, SessionCookie, SessionIdentity


def test_save_csv_wizard_statement_format_composes_public_multipart_request() -> None:
    run_state = RunState("ba-api-test-20260605T123456Z-1a2b3c4d")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": 123, "createdAt": "2026-06-05T12:35:00Z"})

    with _mock_gateway_client(handler) as client:
        created = save_csv_wizard_statement_format(client, run_state, "save-format")

    assert created.id == 123
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/statement-formats/csv-wizard/save"
    parts = _multipart_parts(request)
    assert set(parts) == {"file", "request"}
    assert parts["file"].get_filename() == "save-format.csv"
    assert _part_payload(parts["file"]).startswith(
        b"Date,Description,Amount,Type,Category\n2026-01-15,ba-api-test-"
    )
    assert _json_part(parts["request"]) == csv_save_request(run_state, "save-format")


def test_preview_transactions_composes_public_params_and_file_request() -> None:
    run_state = RunState("ba-api-test-20260605T123456Z-1a2b3c4d")
    account_id = generated_account_id(run_state, "preview")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "previewImportToken": "preview-token",
                "statementFormatId": 321,
                "transactions": [],
            },
        )

    with _mock_gateway_client(handler) as client:
        preview = preview_transactions(
            client,
            run_state,
            "preview",
            statement_format_id=321,
            account_id=account_id,
        )

    assert preview["previewImportToken"] == "preview-token"
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/transactions/preview"
    assert request.url.params["statementFormatId"] == "321"
    assert request.url.params["accountId"] == account_id
    parts = _multipart_parts(request)
    assert set(parts) == {"file"}
    assert parts["file"].get_filename() == "preview.csv"


def test_import_generated_transaction_composes_public_json_batch_request() -> None:
    run_state = RunState("ba-api-test-20260605T123456Z-1a2b3c4d")
    account_id = generated_account_id(run_state, "import")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/statement-formats/csv-wizard/save":
            return httpx.Response(201, json={"id": 456})
        if request.url.path == "/api/v1/transactions/preview":
            return httpx.Response(
                200,
                json={
                    "previewImportToken": "import-token",
                    "statementFormatId": 456,
                    "transactions": [{"description": "preview row"}],
                },
            )
        return httpx.Response(
            200,
            json={
                "transactions": [
                    {
                        "id": 789,
                        "accountId": account_id,
                        "description": f"{run_state.run_id} import transaction",
                    }
                ]
            },
        )

    with _mock_gateway_client(handler) as client:
        created = import_generated_transaction(client, run_state, "import")

    assert created.id == 789
    batch_request = requests[2]
    assert batch_request.method == "POST"
    assert batch_request.url.path == "/api/v1/transactions/batch"
    assert _json_body(batch_request) == batch_import_request(
        preview_import_token="import-token",
        transactions=[import_transaction_request(run_state, "import", account_id=account_id)],
    )


def test_create_saved_view_composes_public_json_request_with_namespaced_criteria() -> None:
    run_state = RunState("ba-api-test-20260605T123456Z-1a2b3c4d")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = _json_body(request)
        assert isinstance(body, dict)
        return httpx.Response(201, json={"id": "view-123", **body})

    with _mock_gateway_client(handler) as client:
        created = create_saved_view(client, run_state, "create-view")

    request = requests[0]
    assert created.id == "view-123"
    assert request.method == "POST"
    assert request.url.path == "/api/v1/views"
    assert _json_body(request) == create_saved_view_request(run_state, "create-view")


def test_view_builders_namespace_default_search_criteria() -> None:
    run_state = RunState("ba-api-test-20260605T123456Z-1a2b3c4d")

    create_payload = create_saved_view_request(run_state, "created")
    update_payload = update_saved_view_request(run_state, "updated")

    assert create_payload["criteria"]["searchText"] == f"{run_state.run_id} created"
    assert update_payload["criteria"]["searchText"] == f"{run_state.run_id} updated"


def _mock_gateway_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> GatewayClient:
    return GatewayClient(
        load_environment("local"),
        session_bundle=SessionBundle(
            primary=SessionIdentity(
                label="primary-session",
                session_cookie=SessionCookie(
                    name="BA_SESSION",
                    value="debug-cookie",
                    domain="",
                    path="/",
                ),
            )
        ),
        transport=httpx.MockTransport(handler),
    )


def _multipart_parts(request: httpx.Request) -> dict[str, MIMEPart]:
    content_type = request.headers["content-type"]
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + request.content
    )
    if not message.is_multipart():
        raise AssertionError(f"expected multipart request, got: {content_type}")

    parts: dict[str, MIMEPart] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            raise AssertionError(f"multipart part has no form name: {part!r}")
        parts[name] = part
    return parts


def _part_payload(part: MIMEPart) -> bytes:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        raise AssertionError(f"expected bytes payload, got: {payload!r}")
    return payload


def _json_part(part: MIMEPart) -> object:
    return json.loads(_part_payload(part).decode("utf-8"))


def _json_body(request: httpx.Request) -> object:
    return json.loads(request.content.decode("utf-8"))
