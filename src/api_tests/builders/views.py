from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from api_tests.run_state import RunState


class CreateSavedViewRequest(TypedDict):
    name: str
    transactionIds: list[int]


class UpdateSavedViewRequest(TypedDict):
    name: str


class UpdateSavedViewTransactionsRequest(TypedDict):
    addTransactionIds: list[int]
    removeTransactionIds: list[int]


class CloneSavedViewRequest(TypedDict):
    name: str


def create_saved_view_request(
    run_state: RunState,
    label: str,
    *,
    transaction_ids: Sequence[int] = (),
) -> CreateSavedViewRequest:
    return {
        "name": f"{run_state.run_id} {label} view",
        "transactionIds": list(transaction_ids),
    }


def update_saved_view_request(
    run_state: RunState,
    label: str,
) -> UpdateSavedViewRequest:
    return {"name": f"{run_state.run_id} {label} updated view"}


def update_saved_view_transactions_request(
    *,
    add_transaction_ids: Sequence[int] = (),
    remove_transaction_ids: Sequence[int] = (),
) -> UpdateSavedViewTransactionsRequest:
    return {
        "addTransactionIds": list(add_transaction_ids),
        "removeTransactionIds": list(remove_transaction_ids),
    }


def clone_saved_view_request(run_state: RunState, label: str) -> CloneSavedViewRequest:
    return {"name": f"{run_state.run_id} {label} cloned view"}
