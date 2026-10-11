from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, NotRequired, TypedDict

from api_tests.run_state import RunState

TransactionType = Literal["CREDIT", "DEBIT"]


class BatchImportTransactionRequest(TypedDict):
    date: str
    description: str
    amount: float
    type: TransactionType
    bankName: str
    currencyIsoCode: str
    category: NotRequired[str]
    accountId: NotRequired[str]
    allowDuplicate: NotRequired[bool]


class BatchImportFileRequest(TypedDict):
    previewImportToken: str
    transactions: list[BatchImportTransactionRequest]


class BatchImportRequest(TypedDict):
    files: list[BatchImportFileRequest]


class CreateTransactionRequest(TypedDict):
    date: str
    description: str
    amount: float
    currencyIsoCode: str
    type: TransactionType
    bankName: NotRequired[str]
    accountId: NotRequired[str]


class TransactionUpdateRequest(TypedDict, total=False):
    description: str
    accountId: str


class BulkDeleteRequest(TypedDict):
    ids: list[int]


def generated_account_id(run_state: RunState, label: str) -> str:
    return f"{run_state.run_id}-{label}-acct"


def generated_bank_name(run_state: RunState, label: str) -> str:
    return f"{run_state.run_id} {label} Bank"


def import_transaction_request(
    run_state: RunState,
    label: str,
    *,
    account_id: str | None = None,
    amount: float = 12.34,
    transaction_type: TransactionType = "DEBIT",
    allow_duplicate: bool = True,
) -> BatchImportTransactionRequest:
    return {
        "date": "2026-01-15",
        "description": f"{run_state.run_id} {label} transaction",
        "amount": amount,
        "type": transaction_type,
        "category": "API Test",
        "bankName": generated_bank_name(run_state, label),
        "currencyIsoCode": "USD",
        "accountId": account_id or generated_account_id(run_state, label),
        "allowDuplicate": allow_duplicate,
    }


def manual_transaction_request(
    run_state: RunState,
    label: str,
    *,
    account_id: str | None = None,
    bank_name: str | None = None,
    amount: float = 12.34,
    transaction_type: TransactionType = "DEBIT",
    date: str = "2026-01-15",
) -> CreateTransactionRequest:
    request: CreateTransactionRequest = {
        "date": date,
        "description": f"{run_state.run_id} {label} transaction",
        "amount": amount,
        "currencyIsoCode": "USD",
        "type": transaction_type,
        "accountId": account_id or generated_account_id(run_state, label),
    }
    if bank_name is not None:
        request["bankName"] = bank_name
    return request


def batch_import_file_request(
    *,
    preview_import_token: str,
    transactions: Sequence[BatchImportTransactionRequest],
) -> BatchImportFileRequest:
    return {
        "previewImportToken": preview_import_token,
        "transactions": list(transactions),
    }


def batch_import_request(
    files: Sequence[BatchImportFileRequest],
) -> BatchImportRequest:
    return {"files": list(files)}


def transaction_update_request(run_state: RunState, label: str) -> TransactionUpdateRequest:
    return {
        "description": f"{run_state.run_id} {label} updated transaction",
        "accountId": generated_account_id(run_state, f"{label}-updated"),
    }


def bulk_delete_request(transaction_ids: Sequence[int]) -> BulkDeleteRequest:
    return {"ids": list(transaction_ids)}
