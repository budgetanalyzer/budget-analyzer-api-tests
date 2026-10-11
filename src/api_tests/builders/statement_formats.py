from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from api_tests.run_state import RunState

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_STATEMENT_FIXTURE_DIR = _REPOSITORY_ROOT / "fixtures" / "statements"

StatementFormatType = Literal["CSV", "PDF", "XLSX"]
StatementFormatScope = Literal["SYSTEM", "USER"]
CsvAmountMode = Literal["SINGLE_AMOUNT_WITH_TYPE", "DEBIT_CREDIT_COLUMNS"]
PdfAmountMode = Literal["SIGNED_AMOUNT", "DEBIT_CREDIT_COLUMNS"]
TransactionDirection = Literal["CREDIT", "DEBIT"]
PdfYearSource = Literal["EXPLICIT_DATE", "STATEMENT_PERIOD"]


class CreateStatementFormatRequest(TypedDict):
    displayName: str
    formatType: StatementFormatType
    bankName: str
    defaultCurrencyIsoCode: str
    scope: NotRequired[StatementFormatScope]
    dateHeader: NotRequired[str]
    dateFormat: NotRequired[str]
    descriptionHeader: NotRequired[str]
    creditHeader: NotRequired[str]
    debitHeader: NotRequired[str]
    typeHeader: NotRequired[str]
    categoryHeader: NotRequired[str]


class UpdateStatementFormatRequest(TypedDict, total=False):
    displayName: str
    bankName: str
    defaultCurrencyIsoCode: str
    enabled: bool


class CsvWizardColumnMappingRequest(TypedDict):
    amountMode: CsvAmountMode
    dateColumn: NotRequired[str]
    dateFormat: NotRequired[str]
    descriptionColumn: NotRequired[str]
    amountColumn: NotRequired[str]
    debitColumn: NotRequired[str]
    creditColumn: NotRequired[str]
    typeColumn: NotRequired[str]
    categoryColumn: NotRequired[str]


class CsvWizardMappingPreviewRequest(TypedDict):
    bankName: str
    defaultCurrencyIsoCode: str
    mapping: CsvWizardColumnMappingRequest
    accountId: NotRequired[str]


class CsvWizardSaveRequest(TypedDict):
    displayName: str
    bankName: str
    defaultCurrencyIsoCode: str
    mapping: CsvWizardColumnMappingRequest


class PdfWizardColumnMappingRequest(TypedDict):
    amountMode: PdfAmountMode
    dateHeader: NotRequired[str]
    dateFormat: NotRequired[str]
    descriptionHeader: NotRequired[str]
    amountHeader: NotRequired[str]
    debitHeader: NotRequired[str]
    creditHeader: NotRequired[str]
    typeHeader: NotRequired[str]
    negativeMeans: NotRequired[TransactionDirection]


class PdfWizardMappingPreviewRequest(TypedDict):
    bankName: str
    defaultCurrencyIsoCode: str
    yearSource: PdfYearSource
    mapping: PdfWizardColumnMappingRequest
    accountId: NotRequired[str]
    headerMustContain: NotRequired[list[str]]
    minimumRows: NotRequired[int]


class PdfWizardSaveRequest(TypedDict):
    displayName: str
    bankName: str
    defaultCurrencyIsoCode: str
    yearSource: PdfYearSource
    mapping: PdfWizardColumnMappingRequest
    headerMustContain: NotRequired[list[str]]
    minimumRows: NotRequired[int]


def create_statement_format_request(
    run_state: RunState,
    label: str,
) -> CreateStatementFormatRequest:
    return {
        "displayName": f"{run_state.run_id} {label} CSV",
        "formatType": "CSV",
        "bankName": f"{run_state.run_id} {label} Bank",
        "defaultCurrencyIsoCode": "USD",
        "scope": "USER",
        "dateHeader": "Date",
        "dateFormat": "uuuu-MM-dd",
        "descriptionHeader": "Description",
        "creditHeader": "Credit",
        "debitHeader": "Debit",
        "categoryHeader": "Category",
    }


def update_statement_format_request(
    run_state: RunState,
    label: str,
) -> UpdateStatementFormatRequest:
    return {
        "displayName": f"{run_state.run_id} {label} Updated CSV",
        "bankName": f"{run_state.run_id} {label} Updated Bank",
        "defaultCurrencyIsoCode": "USD",
        "enabled": True,
    }


def csv_sample_bytes(run_state: RunState, label: str) -> bytes:
    return (
        "Date,Description,Amount,Type,Category\n"
        f"2026-01-15,{run_state.run_id} {label} debit,12.34,DEBIT,API Test\n"
        f"2026-01-16,{run_state.run_id} {label} credit,5.67,CREDIT,API Test\n"
    ).encode()


def basic_csv_sample_bytes() -> bytes:
    return (_STATEMENT_FIXTURE_DIR / "basic.csv").read_bytes()


def csv_wizard_mapping() -> CsvWizardColumnMappingRequest:
    return {
        "dateColumn": "Date",
        "dateFormat": "uuuu-MM-dd",
        "descriptionColumn": "Description",
        "amountMode": "SINGLE_AMOUNT_WITH_TYPE",
        "amountColumn": "Amount",
        "typeColumn": "Type",
        "categoryColumn": "Category",
    }


def csv_preview_request(
    run_state: RunState,
    label: str,
    *,
    account_id: str | None = None,
) -> CsvWizardMappingPreviewRequest:
    request: CsvWizardMappingPreviewRequest = {
        "bankName": f"{run_state.run_id} {label} Bank",
        "defaultCurrencyIsoCode": "USD",
        "mapping": csv_wizard_mapping(),
    }
    if account_id is not None:
        request["accountId"] = account_id
    return request


def csv_save_request(run_state: RunState, label: str) -> CsvWizardSaveRequest:
    return {
        "displayName": f"{run_state.run_id} {label} Wizard CSV",
        "bankName": f"{run_state.run_id} {label} Bank",
        "defaultCurrencyIsoCode": "USD",
        "mapping": csv_wizard_mapping(),
    }


def pdf_sample_bytes() -> bytes:
    return (_STATEMENT_FIXTURE_DIR / "basic.pdf").read_bytes()


def pdf_wizard_mapping() -> PdfWizardColumnMappingRequest:
    return {
        "dateHeader": "Date",
        "dateFormat": "uuuu-MM-dd",
        "descriptionHeader": "Description",
        "amountMode": "SIGNED_AMOUNT",
        "amountHeader": "Amount",
        "negativeMeans": "DEBIT",
    }


def pdf_preview_request(
    run_state: RunState,
    label: str,
    *,
    account_id: str | None = None,
) -> PdfWizardMappingPreviewRequest:
    request: PdfWizardMappingPreviewRequest = {
        "bankName": f"{run_state.run_id} {label} Bank",
        "defaultCurrencyIsoCode": "USD",
        "yearSource": "EXPLICIT_DATE",
        "headerMustContain": ["Date", "Description", "Amount"],
        "minimumRows": 1,
        "mapping": pdf_wizard_mapping(),
    }
    if account_id is not None:
        request["accountId"] = account_id
    return request


def pdf_save_request(run_state: RunState, label: str) -> PdfWizardSaveRequest:
    return {
        "displayName": f"{run_state.run_id} {label} Wizard PDF",
        "bankName": f"{run_state.run_id} {label} Bank",
        "defaultCurrencyIsoCode": "USD",
        "yearSource": "EXPLICIT_DATE",
        "headerMustContain": ["Date", "Description", "Amount"],
        "minimumRows": 1,
        "mapping": pdf_wizard_mapping(),
    }


def multipart_json(value: object) -> tuple[None, str, str]:
    return (None, json.dumps(value, sort_keys=True), "application/json")
