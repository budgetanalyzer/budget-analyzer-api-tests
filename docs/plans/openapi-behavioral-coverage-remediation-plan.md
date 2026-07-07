# Plan: OpenAPI Behavioral Coverage Remediation

Date: 2026-07-07
Status: Implementation pending

Reviewed inputs:

- `docs/plans/openapi-black-box-api-test-repository-plan.md`
- `docs/plans/openapi-placeholder-remediation-plan.md`
- `schemas/openapi.json`
- `schemas/deferred-admin-operations.yaml`
- `src/api_tests/`
- `tests/`
- `fixtures/statements/`
- `tools/`
- `README.md`

## Goal

Close the implementation gaps left after placeholder removal so OpenAPI marker
coverage reflects meaningful black-box behavior coverage.

This plan keeps the existing safety model:

- All API calls continue to go through `GatewayClient`.
- No service-internal database, Redis, RabbitMQ, Kubernetes, or service-DNS
  setup paths are added.
- Local and staging mutation tests use generated normal users and public API
  setup paths.
- Production defaults remain read-only and use `env_cookie`.
- Deferred admin/global operations remain deferred.

Phase 7 CI and self-hosted runner integration is intentionally out of scope for
this plan. Those workflows should be added only when the repository is ready to
be consumed as a deployment gate.

## Current Validation Baseline

The following non-live checks currently pass:

```bash
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
.venv/bin/python -m pytest --env local tests/auth tests/contract tests/tools
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
```

The passing coverage gate proves that every non-deferred operation has a
non-placeholder marker. It does not prove that every marked test asserts the
planned behavior.

## Findings To Remediate

1. PDF wizard tests allow parse errors as acceptable outcomes. The checked-in
   fixture directory contains only `.gitkeep`, and `pdf_sample_bytes()` returns
   a minimal PDF header rather than a deterministic parseable statement.
2. `src/api_tests/schemas.py` is effectively empty. Endpoint tests do not
   validate documented JSON `2xx` response bodies against OpenAPI schemas.
3. Cross-cutting black-box checks are missing:
   - unauthenticated `/api/*` edge authorization failure
   - unknown API route fail-closed behavior
   - non-admin cross-user resource isolation
4. Some endpoint tests assert only a status code or shallow response shape and
   miss planned postconditions, such as verifying deletes with a subsequent
   `404`, confirming hide/unhide list visibility, and checking pin/exclude
   state after saved-view operations.
5. Validation and negative-path tests are incomplete for malformed JSON,
   missing multipart files, empty bulk payloads, missing required fields, and
   invalid enum values.
6. Coverage tooling and contract tests default to the checked-in snapshot. Live
   OpenAPI coverage is available only through an explicit `--source live` flag,
   and the staging-style command in the README does not use it.

## Implementation Principles

- Prefer small, local assertion helpers over broad abstractions.
- Validate documented JSON `2xx` responses first. Do not invent schemas for
  undocumented error bodies.
- For expected error tests, assert the documented status code set from
  `schemas/openapi.json` and a stable error shape only when the contract
  documents one.
- Keep generated user-owned residue scoped to the per-run Auth0 users. Do not
  add cleanup registries, janitors, or Auth0 user deletion.
- Keep production-safe markers only on read-only tests that require no
  generated data or mutating setup.
- Do not weaken tests by accepting broad error ranges when a successful
  behavior is the thing under test.

## Phase 1: OpenAPI Schema Assertion Foundation

Goal: make schema validation available and prove it with unit tests before
touching endpoint tests.

Steps:

1. Implement `src/api_tests/schemas.py` with:
   - `assert_response_matches_openapi(response, operation_id, expected_status, openapi_doc)`
   - JSON content-type validation for documented JSON responses
   - schema lookup through `operation_response_schema`
   - `$ref` resolution against the loaded OpenAPI document
   - clear assertion messages naming the operation id, status, and schema path
2. Use `jsonschema` and `referencing`, which are already project dependencies.
3. Treat missing documented schema for a `2xx` JSON response as a test harness
   failure unless the operation explicitly has no JSON content.
4. Add a pytest fixture for the snapshot OpenAPI document, such as
   `openapi_snapshot`, so endpoint tests can validate without live network
   access during collection.
5. Add unit tests covering:
   - matching object response
   - matching array response
   - scalar response
   - missing operation id
   - undocumented status
   - schema validation failure message
   - response with no JSON content for `204`

Acceptance checks:

```bash
.venv/bin/python -m pytest --env local tests/contract tests/tools
.venv/bin/python -m mypy src tests
```

## Phase 2: Deterministic Statement Fixtures

Goal: replace synthetic in-memory fixture shortcuts with deterministic
statement fixtures that exercise real parser behavior.

Steps:

1. Add `fixtures/statements/basic.csv` with deterministic headers and rows:
   `Date,Description,Amount,Type,Category`, one debit row, and one credit row.
2. Add `fixtures/statements/basic.pdf` as a valid, checked-in PDF containing a
   small table with date, description, and amount columns that the PDF wizard
   can parse.
3. Update statement-format helper code to read fixture bytes with
   `Path.read_bytes()` from repository-relative paths.
4. Keep run-id-specific CSV generation only where uniqueness materially helps
   transaction import tests. Otherwise use the checked-in CSV fixture.
5. Remove the minimal-header `pdf_sample_bytes()` behavior.
6. Update PDF wizard tests so success is required:
   - `analyzePdfSample` expects `200`
   - `previewPdfMapping` expects `200`
   - `savePdfWizardFormat` expects `201`
7. Assert meaningful success response fields:
   - analysis includes candidate rows or headers
   - preview includes transaction rows
   - saved format is `PDF`, user-scoped where exposed, and visible through
     `GET /v1/statement-formats`
8. Keep separate negative tests for invalid PDF input. Those tests may expect
   documented `400` or `422`, but they must not carry the happy-path operation
   coverage alone.

Acceptance checks:

```bash
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
```

Live acceptance:

```bash
.venv/bin/python -m pytest --env local tests/statement_formats -m mutation
```

## Phase 3: Apply Schema Assertions To Endpoint Tests

Goal: ensure successful endpoint responses conform to the OpenAPI contract.

Steps:

1. Add `assert_response_matches_openapi` calls to every endpoint test that
   receives a documented JSON `2xx` response.
2. Apply schema validation before local field assertions so contract failures
   are reported clearly.
3. Cover the read-only production-safe tests first:
   - currencies
   - exchange rates
   - statement format listing
   - transaction list/search/count
   - saved view listing
   - `/api-docs/openapi.json` smoke shape
4. Then cover mutating tests:
   - statement format create/update and wizard save/preview/analyze
   - transaction preview/batch/get/update/bulk delete
   - saved view create/get/update/get transactions/pin/exclude/bulk operations
5. Do not schema-validate `204` responses as JSON. Assert no content.
6. For tests that currently accept documented business errors, split happy-path
   behavior and error behavior into separate tests when practical.

Acceptance checks:

```bash
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
.venv/bin/python -m mypy src tests
```

Live acceptance:

```bash
.venv/bin/python -m pytest --env local -m readonly
.venv/bin/python -m pytest --env local -m mutation
```

## Phase 4: Cross-Cutting Gateway Safety Tests

Goal: cover the public gateway behaviors that are independent of individual
resource workflows.

Steps:

1. Add `tests/auth/test_edge_auth.py`.
2. In the edge-auth test, construct a `GatewayClient` without a session cookie
   and call `GET /v1/currencies` through `api_request`.
3. Assert:
   - status is `401` or the exact documented unauthenticated status
   - response is not the frontend HTML shell
   - no Auth0 test users are created for this test
4. Add `tests/contract/test_unknown_routes.py`.
5. In the unknown-route test, use an authenticated `gateway_client` and call
   `GET /v1/__openapi_black_box_missing__` through `api_request`.
6. Assert:
   - the route fails closed with the documented API error status
   - response is not the frontend HTML shell
   - response content type is not `text/html`
7. Add focused assertion helpers for "not SPA HTML" if repeated.

Acceptance checks:

```bash
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
```

Live acceptance:

```bash
.venv/bin/python -m pytest --env local tests/auth/test_edge_auth.py tests/contract/test_unknown_routes.py
```

## Phase 5: Endpoint Behavioral Postconditions

Goal: make each implemented operation test assert the behavior promised by the
original repository plan, not just marker coverage.

Steps:

1. Transactions:
   - after `deleteTransaction`, call `GET /v1/transactions/{id}` and assert
     documented `404`
   - for `bulkDeleteTransactions`, import at least two transactions, bulk
     delete both ids, and verify both are no longer fetchable
   - assert `batchImportTransactions` accepted or created count matches the
     controlled input size
   - assert `previewTransactions` returns preview rows with expected date,
     description, amount, and account context when exposed
2. Statement formats:
   - after `hideFormat`, verify default listing omits the hidden format
   - after `unhideFormat`, verify default listing includes the restored format
   - after `updateFormat`, fetch the format again and assert persisted fields
   - for `getFormat`, add a documented unknown-id `404` check
3. Saved views:
   - after `deleteView`, call `GET /v1/views/{id}` and assert documented `404`
   - for `getViewTransactions`, assert the imported transaction appears in the
     expected membership group
   - after pin/unpin and exclude/unexclude operations, fetch view transactions
     and assert the id moved into or out of the expected group
   - for bulk pin and bulk exclude, operate on at least two generated
     transaction ids
   - assert bulk operation counts match the controlled input size
4. Read-only resource tests:
   - keep production-safe tests read-only
   - add unknown-id `404` checks only where they do not require mutating setup
     and are safe in production

Acceptance checks:

```bash
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
```

Live acceptance:

```bash
.venv/bin/python -m pytest --env local tests/transactions tests/statement_formats tests/views
```

## Phase 6: Validation And Negative-Path Tests

Goal: cover documented validation failures without broadening production
permissions or relying on service internals.

Steps:

1. Add validation tests near the domain they exercise.
2. Mark validation tests with the same safety markers as the request they make:
   - `mutation` when setup creates or updates data
   - `destructive` when delete, hide, import, or bulk-delete behavior is used
   - no `production_safe` marker for tests requiring generated data or
     mutating setup
3. Transactions:
   - `previewTransactions` without a multipart file returns documented
     validation status
   - `batchImportTransactions` with `transactions=[]` returns documented
     validation status
   - `bulkDeleteTransactions` with `ids=[]` returns documented validation
     status
   - malformed JSON on a JSON endpoint returns documented `400` behavior
4. Statement formats:
   - `createFormat` missing a required field returns documented validation
     status
   - `updateFormat` with an invalid enum or invalid payload shape returns
     documented validation status
   - CSV and PDF wizard preview/save requests missing the JSON `request` part
     return documented validation status
5. Saved views:
   - `createView` missing `name` or `criteria` returns documented validation
     status
   - `updateView` with invalid criteria returns documented validation status
   - bulk pin/exclude with `ids=[]` returns documented validation status
6. Add a small helper that asserts a documented validation status and stable
   error fields, without assuming undocumented service-internal error details.

Acceptance checks:

```bash
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
```

Live acceptance:

```bash
.venv/bin/python -m pytest --env local -m "mutation or destructive"
```

## Phase 7: Non-Admin Authorization Coverage

Goal: implement the non-admin authorization coverage from the repository plan
without introducing admin credentials or cleanup logic.

Steps:

1. Add fixtures for secondary-user API access:
   - `secondary_gateway_client` using `auth_context.identities.secondary_user`
   - skip with a clear reason when the selected auth mode does not provide
     generated identities, such as production `env_cookie`
2. Add `tests/authorization/test_cross_user_resources.py`.
3. Use only public API setup paths:
   - primary user creates statement formats, transactions, and saved views
   - secondary user attempts direct access by id
4. Assert the documented isolation behavior, either `403` or `404` according
   to the OpenAPI contract and observed public API behavior.
5. Cover at least:
   - secondary cannot fetch primary transaction by id
   - secondary cannot fetch primary saved view by id
   - secondary cannot fetch primary statement format by id if user ownership
     applies to that resource
   - secondary cannot pin or exclude a primary transaction into a secondary
     saved view
   - secondary cannot mutate primary saved view pin/exclude state
6. Mark these tests with `authorization` and with `mutation` when public setup
   creates resources.
7. Do not call `/v1/users`, `/v1/transactions/search/count`, currency write
   endpoints, or exchange-rate import.

Acceptance checks:

```bash
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
```

Live acceptance:

```bash
.venv/bin/python -m pytest --env staging -m authorization
```

## Phase 8: Live OpenAPI Drift Coverage

Goal: make staging-style coverage check the deployed OpenAPI contract while
preserving snapshot-only checks for local non-live development.

Steps:

1. Keep snapshot coverage as the default for local offline commands.
2. Add a documented staging gate command that uses live OpenAPI explicitly:

   ```bash
   .venv/bin/python tools/check-openapi-coverage.py --env staging --source live --fail-missing --fail-placeholder
   ```

3. Consider adding `--compare-snapshot` to `check-openapi-coverage.py` so a
   live run can report operation drift between the deployed contract and
   `schemas/openapi.json`.
4. If `--compare-snapshot` is added, make it report:
   - operations present live but absent from the snapshot
   - operations present in the snapshot but absent live
   - method or path changes for the same operation id
5. Do not make ordinary `pytest --collect-only` require a live target.
6. Update `README.md` and this repository's operational docs to distinguish:
   - local offline marker coverage against the snapshot
   - live staging coverage against deployed `/api-docs/openapi.json`
   - intentional snapshot refresh through `tools/refresh-openapi.py`

Acceptance checks:

```bash
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
.venv/bin/python tools/export-openapi-coverage.py --env local
```

Live acceptance:

```bash
.venv/bin/python tools/check-openapi-coverage.py --env staging --source live --fail-missing --fail-placeholder
```

## Phase 9: Documentation And Final Validation

Goal: keep behavior, commands, and safety expectations synchronized.

Steps:

1. Update `README.md` when commands or live/snapshot coverage behavior changes.
2. Update docs for:
   - schema assertion behavior
   - statement fixture expectations
   - validation-test marker conventions
   - authorization-test prerequisites
   - live OpenAPI drift command
3. Keep `docs/plans/openapi-placeholder-remediation-plan.md` intact as the
   historical placeholder-removal plan. Do not rewrite its validation results;
   this plan supersedes it for remaining behavioral remediation.
4. Re-run the full non-live gate:

   ```bash
   .venv/bin/python -m ruff format --check .
   .venv/bin/python -m ruff check .
   .venv/bin/python -m mypy src tests
   .venv/bin/python -m pytest --env local --collect-only
   .venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
   .venv/bin/python -m pytest --env local tests/auth tests/contract tests/tools
   .venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
   ```

5. Run live endpoint checks before declaring the behavioral remediation
   complete:

   ```bash
   .venv/bin/python -m pytest --env local -m readonly
   .venv/bin/python -m pytest --env local -m "mutation or destructive"
   .venv/bin/python -m pytest --env staging -m authorization
   .venv/bin/python tools/check-openapi-coverage.py --env staging --source live --fail-missing --fail-placeholder
   ```

## Completion Criteria

This plan is complete when:

- Every non-deferred operation still has non-placeholder OpenAPI marker
  coverage.
- Happy-path PDF wizard tests require successful parse/save behavior.
- Documented JSON `2xx` responses are schema-validated.
- Gateway edge-auth and unknown-route behavior are covered.
- Non-admin cross-user isolation is covered with generated normal users.
- Delete, hide/unhide, pin/unpin, exclude/unexclude, and bulk operations assert
  their observable postconditions.
- Validation failures are covered for representative JSON and multipart
  request errors.
- Staging-style OpenAPI coverage can be run against live `/api-docs`.
- Production smoke collection still selects only read-only production-safe
  tests and does not create per-run users.
