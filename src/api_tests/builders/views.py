from __future__ import annotations

from collections.abc import Sequence
from typing import NotRequired, TypedDict

from api_tests.run_state import RunState


class ViewCriteriaRequest(TypedDict, total=False):
    dateFrom: str
    dateTo: str
    accountIds: list[str]
    bankNames: list[str]
    currencyIsoCodes: list[str]
    minAmount: float
    maxAmount: float
    type: str
    searchText: str


class CreateSavedViewRequest(TypedDict):
    name: str
    criteria: ViewCriteriaRequest
    openEnded: NotRequired[bool]


class UpdateSavedViewRequest(TypedDict, total=False):
    name: str
    criteria: ViewCriteriaRequest
    openEnded: bool


class BulkViewTransactionRequest(TypedDict):
    ids: list[int]


def view_criteria(
    *,
    account_id: str | None = None,
    search_text: str | None = None,
) -> ViewCriteriaRequest:
    criteria: ViewCriteriaRequest = {
        "dateFrom": "2026-01-01",
        "dateTo": "2026-12-31",
        "currencyIsoCodes": ["USD"],
    }
    if account_id is not None:
        criteria["accountIds"] = [account_id]
    if search_text is not None:
        criteria["searchText"] = search_text
    return criteria


def create_saved_view_request(
    run_state: RunState,
    label: str,
    *,
    account_id: str | None = None,
    search_text: str | None = None,
) -> CreateSavedViewRequest:
    return {
        "name": f"{run_state.run_id} {label} view",
        "criteria": view_criteria(
            account_id=account_id,
            search_text=search_text or f"{run_state.run_id} {label}",
        ),
        "openEnded": False,
    }


def update_saved_view_request(
    run_state: RunState,
    label: str,
    *,
    account_id: str | None = None,
    search_text: str | None = None,
) -> UpdateSavedViewRequest:
    return {
        "name": f"{run_state.run_id} {label} updated view",
        "criteria": view_criteria(
            account_id=account_id,
            search_text=search_text or f"{run_state.run_id} {label}",
        ),
        "openEnded": False,
    }


def bulk_view_transaction_request(transaction_ids: Sequence[int]) -> BulkViewTransactionRequest:
    return {"ids": list(transaction_ids)}
