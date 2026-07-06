# Plan: OpenAPI Placeholder Remediation

Date: 2026-07-06
Status: Implemented

## Goal

Replace the 36 marker-only OpenAPI placeholder tests with black-box tests that
exercise the public gateway API through `GatewayClient`, while preserving the
production read-only safety model and the per-run generated-user data boundary.

This plan is scoped to the non-deferred operations in `schemas/openapi.json`.
The seven admin/global operations listed in
`schemas/deferred-admin-operations.yaml` remain deferred.

## Current Placeholder Inventory

- Currencies: `getAll`, `getById`
- Exchange rates: `getExchangeRates`
- Statement formats: `listFormats`, `createFormat`, `getFormat`,
  `updateFormat`, `hideFormat`, `unhideFormat`, `analyzeCsvSample`,
  `previewCsvMapping`, `saveCsvWizardFormat`, `analyzePdfSample`,
  `previewPdfMapping`, `savePdfWizardFormat`
- Transactions: `getTransactions`, `searchTransactions`, `countTransactions`,
  `getTransaction`, `updateTransaction`, `deleteTransaction`,
  `previewTransactions`, `batchImportTransactions`, `bulkDeleteTransactions`
- Saved views: `listViews`, `createView`, `getView`, `updateView`,
  `deleteView`, `getViewTransactions`, `pinTransaction`, `unpinTransaction`,
  `bulkPinTransactions`, `excludeTransaction`, `unexcludeTransaction`,
  `bulkExcludeTransactions`

## Implementation Steps

1. Add typed request builders for generated statement formats, transactions,
   and saved views.
2. Add small response/assertion helpers where repeated status-code and JSON
   shape checks would otherwise drift across tests.
3. Replace placeholders with real tests by domain:
   - Keep pure list/read operations marked `readonly` and, where safe,
     `production_safe`.
   - Mark tests that create fixture data as `mutation`, even when the OpenAPI
     operation under test is a read operation.
   - Keep delete, hide, import, and bulk-delete workflows marked
     `destructive` where they alter persisted user-owned state.
4. Use only public API setup paths. Create prerequisite formats, preview
   tokens, imported transactions, and saved views through gateway requests.
5. Keep production execution read-only. Do not add production-safe markers to
   tests that require generated data or mutating setup.
6. Update coverage/tool tests and docs so `--fail-placeholder` is expected to
   pass once placeholders are gone.

## Acceptance Checks

```bash
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
```

Live execution of the new endpoint tests still requires the selected deployed
environment, Auth0 prerequisites, browser login, and network access.

## Validation Results

Commands run from this repository:

```bash
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
.venv/bin/python -m pytest --env local tests/auth tests/contract tests/tools
.venv/bin/python tools/validate-environment.py --env local
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
```

Observed results:

- Ruff, mypy, local collection, non-live tests, and environment validation pass.
- OpenAPI coverage reports `operations: 43`, `missing: 0`,
  `placeholder: 0`, and `deferred_admin: 7`.
- Production smoke collection selects 9 read-only production-safe tests and
  deselects the mutating endpoint tests.
