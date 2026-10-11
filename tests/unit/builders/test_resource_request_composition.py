from __future__ import annotations

import json
from collections.abc import Callable
from email import policy
from email.message import MIMEPart
from email.parser import BytesParser

import httpx

from api_tests.builders.statement_formats import csv_save_request
from api_tests.builders.transactions import (
    generated_account_id,
    generated_bank_name,
    manual_transaction_request,
)
from api_tests.builders.views import (
    clone_saved_view_request,
    create_saved_view_request,
    update_saved_view_request,
    update_saved_view_transactions_request,
)
from api_tests.client import GatewayClient
from api_tests.config import load_environment
from api_tests.resources import (
    create_manual_transaction,
    create_saved_view,
    preview_transaction_files,
    save_csv_wizard_statement_format,
)
from api_tests.run_state import RunState
from api_tests.session import Session


def test_save_csv_wizard_statement_format_composes_documented_multipart_request() -> None:
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
    assert len(parts["file"]) == 1
    assert parts["file"][0].get_filename() == "save-format.csv"
    assert _part_payload(parts["file"][0]).startswith(
        b"Date,Description,Amount,Type,Category\n2026-01-15,ba-api-test-"
    )
    assert _json_part(parts["request"][0]) == csv_save_request(run_state, "save-format")


def test_preview_transaction_files_composes_repeated_ordered_file_parts() -> None:
    run_state = RunState("ba-api-test-20260605T123456Z-1a2b3c4d")
    account_id = generated_account_id(run_state, "preview")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"files": []})

    with _mock_gateway_client(handler) as client:
        preview = preview_transaction_files(
            client,
            run_state,
            ("preview-first", "preview-second"),
            statement_format_id=321,
            account_id=account_id,
        )

    assert preview == {"files": []}
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/transactions/preview"
    assert request.url.params["statementFormatId"] == "321"
    assert request.url.params["accountId"] == account_id
    parts = _multipart_parts(request)
    assert set(parts) == {"files"}
    assert [part.get_filename() for part in parts["files"]] == [
        "preview-first.csv",
        "preview-second.csv",
    ]


def test_create_manual_transaction_composes_one_direct_public_request() -> None:
    run_state = RunState("ba-api-test-20260605T123456Z-1a2b3c4d")
    account_id = generated_account_id(run_state, "manual")
    bank_name = generated_bank_name(run_state, "manual")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = _json_body(request)
        assert isinstance(body, dict)
        return httpx.Response(201, json={"id": 789, **body})

    with _mock_gateway_client(handler) as client:
        created = create_manual_transaction(client, run_state, "manual")

    assert created.id == 789
    assert created.account_id == account_id
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/transactions"
    assert _json_body(request) == manual_transaction_request(
        run_state,
        "manual",
        account_id=account_id,
        bank_name=bank_name,
    )


def test_create_manual_transaction_can_omit_optional_bank_name() -> None:
    run_state = RunState("ba-api-test-20260605T123456Z-1a2b3c4d")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = _json_body(request)
        assert isinstance(body, dict)
        return httpx.Response(201, json={"id": 790, **body})

    with _mock_gateway_client(handler) as client:
        created = create_manual_transaction(
            client, run_state, "manual-no-bank", include_bank_name=False
        )

    assert "bankName" not in created.body
    request_body = _json_body(requests[0])
    assert isinstance(request_body, dict)
    assert "bankName" not in request_body


def test_create_saved_view_composes_explicit_transaction_ids() -> None:
    run_state = RunState("ba-api-test-20260605T123456Z-1a2b3c4d")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = _json_body(request)
        assert isinstance(body, dict)
        return httpx.Response(201, json={"id": "view-123", **body})

    with _mock_gateway_client(handler) as client:
        created = create_saved_view(client, run_state, "create-view", transaction_ids=(45, 12))

    request = requests[0]
    assert created.id == "view-123"
    assert request.method == "POST"
    assert request.url.path == "/api/v1/views"
    assert _json_body(request) == create_saved_view_request(
        run_state, "create-view", transaction_ids=(45, 12)
    )


def test_static_view_builders_match_current_request_shapes() -> None:
    run_state = RunState("ba-api-test-20260605T123456Z-1a2b3c4d")

    assert create_saved_view_request(run_state, "created", transaction_ids=(3, 1)) == {
        "name": f"{run_state.run_id} created view",
        "transactionIds": [3, 1],
    }
    assert update_saved_view_request(run_state, "updated") == {
        "name": f"{run_state.run_id} updated updated view"
    }
    assert update_saved_view_transactions_request(
        add_transaction_ids=(2, 3), remove_transaction_ids=(1,)
    ) == {
        "addTransactionIds": [2, 3],
        "removeTransactionIds": [1],
    }
    assert clone_saved_view_request(run_state, "copy") == {
        "name": f"{run_state.run_id} copy cloned view"
    }


def _mock_gateway_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> GatewayClient:
    return GatewayClient(
        load_environment("local"),
        session=Session(cookie_name="BA_SESSION", cookie_value="debug-cookie"),
        transport=httpx.MockTransport(handler),
    )


def _multipart_parts(request: httpx.Request) -> dict[str, list[MIMEPart]]:
    content_type = request.headers["content-type"]
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + request.content
    )
    if not message.is_multipart():
        raise AssertionError(f"expected multipart request, got: {content_type}")

    parts: dict[str, list[MIMEPart]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            raise AssertionError(f"multipart part has no form name: {part!r}")
        parts.setdefault(name, []).append(part)
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
