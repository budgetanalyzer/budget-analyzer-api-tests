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


class BatchImportRequest(TypedDict):
    previewImportToken: str
    transactions: list[BatchImportTransactionRequest]


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
        "allowDuplicate": True,
    }


def batch_import_request(
    *,
    preview_import_token: str,
    transactions: Sequence[BatchImportTransactionRequest],
) -> BatchImportRequest:
    return {
        "previewImportToken": preview_import_token,
        "transactions": list(transactions),
    }


def transaction_update_request(run_state: RunState, label: str) -> TransactionUpdateRequest:
    return {
        "description": f"{run_state.run_id} {label} updated transaction",
        "accountId": generated_account_id(run_state, f"{label}-updated"),
    }


def bulk_delete_request(transaction_ids: Sequence[int]) -> BulkDeleteRequest:
    return {"ids": list(transaction_ids)}
