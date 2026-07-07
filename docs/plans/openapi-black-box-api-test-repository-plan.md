# Plan: OpenAPI Black-Box API Test Repository

Date: 2026-06-04
Status: Detailed implementation plan

Related documents:

- `docs/plans/self-hosted-staging-runner-cd-plan.md`
- `docs-aggregator/README.md`
- `docs-aggregator/openapi.json`
- `docs/architecture/session-edge-authorization-pattern.md`
- `docs/architecture/resource-routing-pattern.md`
- `docs/setup/auth0-setup.md`
- `nginx/README.md`
- `scripts/repo/generate-unified-api-docs.sh`

## Scope

This plan defines a standalone black-box API test repository for the public
operations in the unified OpenAPI contract served through `/api-docs`.

The target repository is separate from service implementation repositories so
it can test the deployed system through the same public API lane used by the
browser:

```text
test runner -> HTTPS ingress -> ext_authz -> NGINX -> services
```

The first consumer is the self-hosted staging runner plan. The repository can
be built before the runner exists and should be usable against local Tilt,
private staging, and production read-only checks.

Initial implementation scope is deliberately non-admin. Admin and global
catalog functionality is tracked as deferred scope because it needs a separate
identity, cleanup, and safety design.

## Direct Answer: Can This Be Done First?

Yes. The standalone API test repository is a good first step and does not need
to wait for the self-hosted staging runner.

The minimum first configuration is a target URL, but a credible suite needs
more than that:

- public origin and API base path, such as `https://app.budgetanalyzer.localhost`
  plus `/api`
- auth/session strategy for obtaining or receiving a valid `BA_SESSION` cookie
- TLS trust behavior for local and staging certificates
- environment type, such as `local`, `staging`, or `production`
- destructive-test policy so mutating tests cannot run against production by
  accident
- seed-data strategy and test identity assumptions
- timeout, retry, and artifact settings for CI diagnostics

The recommended first version should automate the normal Auth0 browser login
flow and treat a pre-created `BA_SESSION` environment variable as a local debug
fallback only.

The primary auth path is:

```text
Playwright -> /oauth2/authorization/idp -> Auth0 Universal Login
  -> /login/oauth2/code/idp -> Session Gateway sets BA_SESSION
```

That path proves the same browser/session lane that real users use. The suite
must not seed Redis sessions, call service-internal auth hooks, or exchange
Auth0 tokens directly for API access.

## Goals

1. Create a standalone test repository that runs outside service repos.
2. Treat `docs-aggregator/openapi.json` or the deployed
   `/api-docs/openapi.json` as the coverage manifest.
3. Exercise every non-admin public operation currently exposed by the unified
   OpenAPI spec.
4. Run through the public gateway path, not direct Kubernetes service DNS.
5. Verify success responses, schema shape, expected validation failures,
   not-found behavior, and authorization boundaries where the contract exposes
   them.
6. Produce CI-friendly JUnit, coverage, request log, and OpenAPI drift
   artifacts.
7. Support local and staging as full mutating environments while keeping
   production checks read-only unless explicitly overridden.
8. Track deferred admin/global operations explicitly so they do not disappear
   from coverage reporting.

## Non-Goals

- Testing service internals or direct in-cluster service URLs.
- Replacing service-local unit, integration, or controller tests.
- Creating orchestration-level workarounds for service-owned API defects.
- Requiring public inbound access to a home staging runner.
- Exercising archived docs or stale test assets.
- Making production data destructive-test capable by default.
- Implementing admin, user-administration, cross-user aggregate, currency
  catalog mutation, or exchange-rate import behavior in the initial suite.

## Proposed Repository Shape

```text
budget-analyzer-api-tests/
  README.md
  pyproject.toml
  pytest.ini
  api_tests/
    __init__.py
    auth.py
    auth0.py
    browser_login.py
    builders/
      __init__.py
      statement_formats.py
      transactions.py
      views.py
    client.py
    config.py
    coverage.py
    identities.py
    logging.py
    openapi.py
    run_state.py
    schemas.py
  environments/
    local.yaml
    staging.yaml
    production.yaml
  fixtures/
    statements/
      basic.csv
      basic.pdf
  schemas/
    deferred-admin-operations.yaml
    openapi.json
  tests/
    conftest.py
    auth/
    api_docs/
    authorization/
    contract/
    currencies/
    exchange_rates/
    statement_formats/
    transactions/
    views/
  tools/
    check-openapi-coverage.py
    export-openapi-coverage.py
    refresh-openapi.py
    validate-environment.py
  artifacts/
    .gitkeep
```

Preferred initial stack:

- `pytest`
- `httpx`
- `playwright` for the real Auth0 browser login flow
- `pydantic` or `jsonschema` for response and payload checks
- `pytest-xdist` only after data isolation is reliable
- `respx` only for unit tests of the test harness itself, not for API tests

## Environment Configuration

Environment files should be declarative and non-secret:

```yaml
name: local
origin: https://app.budgetanalyzer.localhost
api_base_path: /api
openapi_path: /api-docs/openapi.json
verify_tls: true
environment_type: local
allow_mutation: true
allow_destructive: true
session_cookie_name: BA_SESSION
auth:
  mode: browser_auth0
  cookie_env: BA_SESSION
  browser:
    headless: true
  auth0:
    management_domain_env: AUTH0_MGMT_DOMAIN
    management_client_id_env: AUTH0_MGMT_CLIENT_ID
    management_client_secret_env: AUTH0_MGMT_CLIENT_SECRET
    connection_env: AUTH0_TEST_CONNECTION
    test_email_domain: api-tests.example.invalid
data:
  namespace_prefix: ba-api-test
  cleanup: none
  per_run_user_boundary: true
timeouts:
  request_seconds: 15
  eventually_seconds: 30
```

Secrets stay outside the YAML. The first version should read them from
environment variables:

- Auth0 Management API inputs named by `auth.auth0.*_env`
- Auth0 database connection name named by `auth.auth0.connection_env`
- optional `BA_SESSION` only for `auth.mode: env_cookie` local debugging

The Auth0 Management API application should be scoped narrowly for test user
provisioning. The initial implementation should need `create:users` and may
need `read:users` or `update:users` depending on how the tenant handles
`email_verified` on create. It should not need `delete:users` because the
initial implementation does not delete generated Auth0 users.

The harness should generate per-run normal-user passwords in memory. It should
not write generated passwords, Auth0 Management API tokens, session cookies, or
credentials to artifacts.

Production should default to:

```yaml
environment_type: production
allow_mutation: false
allow_destructive: false
```

## OpenAPI Coverage Model

The suite should treat the current OpenAPI document as executable coverage
input.

Required harness behavior:

1. Load the target OpenAPI document from the deployed environment.
2. Optionally compare it with the checked-in `schemas/openapi.json` snapshot.
3. Build an operation inventory from `paths`.
4. Require every non-deferred `operationId` to map to at least one test marker.
5. Load `schemas/deferred-admin-operations.yaml` and mark those operations as
   deferred admin/global scope.
6. Fail CI when a new non-deferred operation appears without an explicit test.
7. Emit a coverage artifact listing operation id, method, path, expected test
   files, deferred status, and execution status.

Coverage marker convention:

```python
@pytest.mark.openapi("createView")
def test_create_view(...):
    ...
```

Placeholders are allowed only while a phase is under construction. They are not
an acceptable completion state for the staging gate.

Deferred admin/global operations are not waivers. They are out of initial scope
and should fail only when the future admin suite is enabled. The initial
deferred set is:

```yaml
deferred_admin_operations:
  - operationId: countTransactionsAcrossUsers
    method: GET
    path: /v1/transactions/search/count
  - operationId: create
    method: POST
    path: /v1/currencies
  - operationId: update
    method: PUT
    path: /v1/currencies/{id}
  - operationId: importLatestExchangeRates
    method: POST
    path: /v1/exchange-rates/import
  - operationId: getUsers
    method: GET
    path: /v1/users
  - operationId: getUser
    method: GET
    path: /v1/users/{id}
  - operationId: deactivateUser
    method: POST
    path: /v1/users/{id}/deactivate
```

## Current Public Operation Inventory

This inventory reflects `docs-aggregator/openapi.json` on 2026-06-04. It is a
planning snapshot only. The implemented suite should regenerate coverage from
OpenAPI at runtime.

### Transactions

| Operation | Method | Path | Primary assertions |
| --- | --- | --- | --- |
| `getTransactions` | `GET` | `/v1/transactions` | Returns a paged transaction response for the authenticated user. |
| `searchTransactions` | `GET` | `/v1/transactions/search` | Supports documented filters, pagination, and sorting. |
| `countTransactions` | `GET` | `/v1/transactions/count` | Returns count consistent with seeded transactions. |
| `countTransactionsAcrossUsers` | `GET` | `/v1/transactions/search/count` | Deferred admin/global operation; not part of the initial non-admin gate. |
| `getTransaction` | `GET` | `/v1/transactions/{id}` | Returns seeded transaction by id and `404` for an unknown id. |
| `updateTransaction` | `PATCH` | `/v1/transactions/{id}` | Applies partial updates and rejects invalid fields. |
| `deleteTransaction` | `DELETE` | `/v1/transactions/{id}` | Deletes an owned transaction and returns `404` after deletion. |
| `previewTransactions` | `POST` | `/v1/transactions/preview` | Accepts representative upload input and reports preview rows or validation errors. |
| `batchImportTransactions` | `POST` | `/v1/transactions/batch` | Imports a deterministic batch and reports accepted/rejected rows. |
| `bulkDeleteTransactions` | `POST` | `/v1/transactions/bulk-delete` | Deletes a controlled seeded set and rejects malformed ids. |

### Statement Formats

| Operation | Method | Path | Primary assertions |
| --- | --- | --- | --- |
| `listFormats` | `GET` | `/v1/statement-formats` | Lists visible formats and honors `includeHidden`. |
| `createFormat` | `POST` | `/v1/statement-formats` | Creates a format and rejects invalid mapping payloads. |
| `getFormat` | `GET` | `/v1/statement-formats/{id}` | Returns a seeded format and `404` for an unknown id. |
| `updateFormat` | `PUT` | `/v1/statement-formats/{id}` | Replaces editable fields and validates required structure. |
| `hideFormat` | `POST` | `/v1/statement-formats/{id}/hide` | Hides a format from default listing. |
| `unhideFormat` | `POST` | `/v1/statement-formats/{id}/unhide` | Restores a hidden format to default listing. |
| `analyzeCsvSample` | `POST` | `/v1/statement-formats/csv-wizard/analyze` | Accepts a fixture CSV and returns detected columns/warnings. |
| `previewCsvMapping` | `POST` | `/v1/statement-formats/csv-wizard/preview` | Applies CSV mapping and returns deterministic preview rows. |
| `saveCsvWizardFormat` | `POST` | `/v1/statement-formats/csv-wizard/save` | Saves a CSV wizard format from fixture input. |
| `analyzePdfSample` | `POST` | `/v1/statement-formats/pdf-wizard/analyze` | Accepts a fixture PDF and returns table candidates. |
| `previewPdfMapping` | `POST` | `/v1/statement-formats/pdf-wizard/preview` | Applies PDF mapping and returns deterministic preview rows. |
| `savePdfWizardFormat` | `POST` | `/v1/statement-formats/pdf-wizard/save` | Saves a PDF wizard format from fixture input. |

### Saved Views

| Operation | Method | Path | Primary assertions |
| --- | --- | --- | --- |
| `listViews` | `GET` | `/v1/views` | Lists views for the authenticated user. |
| `createView` | `POST` | `/v1/views` | Creates a view with criteria and rejects invalid criteria. |
| `getView` | `GET` | `/v1/views/{id}` | Returns a seeded view and `404` for unknown id. |
| `updateView` | `PUT` | `/v1/views/{id}` | Updates name and criteria. |
| `deleteView` | `DELETE` | `/v1/views/{id}` | Deletes a view and verifies subsequent `404`. |
| `getViewTransactions` | `GET` | `/v1/views/{id}/transactions` | Returns transactions matching view criteria plus pin/exclude state. |
| `pinTransaction` | `POST` | `/v1/views/{id}/pin/{txnId}` | Pins one seeded transaction. |
| `unpinTransaction` | `DELETE` | `/v1/views/{id}/pin/{txnId}` | Removes one pinned transaction. |
| `bulkPinTransactions` | `POST` | `/v1/views/{id}/pin` | Pins multiple seeded transactions. |
| `excludeTransaction` | `POST` | `/v1/views/{id}/exclude/{txnId}` | Excludes one seeded transaction. |
| `unexcludeTransaction` | `DELETE` | `/v1/views/{id}/exclude/{txnId}` | Removes one excluded transaction. |
| `bulkExcludeTransactions` | `POST` | `/v1/views/{id}/exclude` | Excludes multiple seeded transactions. |

### Currency And Exchange Rates

| Operation | Method | Path | Primary assertions |
| --- | --- | --- | --- |
| `getAll` | `GET` | `/v1/currencies` | Lists currencies and honors `enabledOnly`. |
| `create` | `POST` | `/v1/currencies` | Deferred admin/global operation; not part of the initial non-admin gate. |
| `getById` | `GET` | `/v1/currencies/{id}` | Returns an existing listed currency series and `404` for unknown id. |
| `update` | `PUT` | `/v1/currencies/{id}` | Deferred admin/global operation; not part of the initial non-admin gate. |
| `getExchangeRates` | `GET` | `/v1/exchange-rates` | Filters by date range and target currency. |
| `importLatestExchangeRates` | `POST` | `/v1/exchange-rates/import` | Deferred admin/global operation; not part of the initial non-admin gate. |

### User Administration And Admin Operations

| Operation | Method | Path | Primary assertions |
| --- | --- | --- | --- |
| `getUsers` | `GET` | `/v1/users` | Deferred admin/global operation; not part of the initial non-admin gate. |
| `getUser` | `GET` | `/v1/users/{id}` | Deferred admin/global operation; not part of the initial non-admin gate. |
| `deactivateUser` | `POST` | `/v1/users/{id}/deactivate` | Deferred admin/global operation; not part of the initial non-admin gate. |

## Cross-Cutting Test Categories

Every non-deferred operation should have at least one happy-path or
expected-result test. User-owned mutating operations use a fresh per-run user
as the isolation boundary. The suite should add the following cross-cutting
tests:

- unauthenticated `/api/*` request returns `401` from the edge
- authenticated request reaches the expected service and does not fall through
  to the SPA
- unknown `/api/v1/...` route fails closed
- malformed JSON returns the documented `400` shape where applicable
- invalid multipart upload returns documented validation status
- response content type is JSON for API responses
- response payloads conform to referenced OpenAPI schemas for `2xx` responses
- destructive tests are blocked when `allow_destructive` is false
- operation coverage fails when the OpenAPI spec gains an untested
  non-deferred operation

## Data And Identity Strategy

The suite needs repeatable data without service-internal setup hooks.

Resolved first approach:

1. Create fresh Auth0 normal users for each local or staging test run.
2. Log in through the real browser Auth0 flow so Session Gateway creates the
   `BA_SESSION` cookie and syncs each app user.
3. Use the primary per-run normal user for transaction, statement-format, and
   saved-view mutation tests.
4. Use a secondary per-run normal user for cross-user isolation tests.
5. Prefix names/descriptions with a unique run id such as
   `ba-api-test-<timestamp>-<short-sha>`.
6. Do not add generic cleanup callbacks, janitors, or best-effort cleanup logic
   in the initial implementation. The per-run user is the data boundary for
   user-owned data.
7. Endpoint tests may still call delete, hide, or unhide when that operation is
   the non-admin behavior under test.
8. Keep production checks read-only and skip create/update/delete/import tests
   unless explicitly enabled.

Admin/global coverage is deferred. This includes user administration,
cross-user aggregate endpoints, currency catalog mutation, and exchange-rate
import. The future admin suite should define its own admin identity,
per-run cleanup, and global-state reset strategy before those operations are
enabled.

## Implementation Phases

Implementation agents should work from the standalone
`budget-analyzer-api-tests/` repository root. They should not add direct
Kubernetes, database, Redis, RabbitMQ, or service-DNS setup paths. Every API
request must be composed as:

```text
{origin}{api_base_path}{openapi path}
```

For example, with the local environment config, OpenAPI path `/v1/currencies`
becomes `https://app.budgetanalyzer.localhost/api/v1/currencies`.

### Phase 1: Repository Bootstrap

Goal: create a runnable Python test harness with no service-specific test
logic yet.

Status: complete. Implemented in sibling repository
`../budget-analyzer-api-tests` through step 19.

Steps:

1. Create the standalone repository directory `budget-analyzer-api-tests/`
   next to the other Budget Analyzer repositories.
2. Add the repository tree shown in `Proposed Repository Shape`, including
   empty package marker files and `artifacts/.gitkeep`.
3. Add `pyproject.toml` for Python 3.12 or newer with these runtime
   dependencies: `pytest`, `httpx`, `playwright`, `pydantic`, `PyYAML`,
   `jsonschema`, and `referencing`.
4. Add these dev dependencies in the same file: `ruff`, `mypy`, `respx`,
   `types-PyYAML`.
5. Add `pytest.ini` with markers `openapi(operation_id)`, `placeholder`,
   `mutation`, `destructive`, `readonly`, `authorization`, and
   `production_safe`.
6. Add `environments/local.yaml`, `environments/staging.yaml`, and
   `environments/production.yaml` using the fields from `Environment
   Configuration`.
7. Set `production.yaml` to `allow_mutation: false` and
   `allow_destructive: false`.
8. Add `api_tests/config.py` with Pydantic models for the environment YAML.
   Validate `name`, `origin`, `api_base_path`, `openapi_path`, `verify_tls`,
   `environment_type`, `allow_mutation`, `allow_destructive`,
   `session_cookie_name`, `auth.mode`, `auth.cookie_env`, `auth.browser`,
   `auth.auth0`, `data.cleanup`, `data.per_run_user_boundary`,
   `timeouts.request_seconds`, and `timeouts.eventually_seconds`.
9. In `tests/conftest.py`, add a pytest `--env` option. Load
   `environments/<env>.yaml` during test configuration and expose it as a
   session-scoped `env_config` fixture.
10. Add `tools/validate-environment.py`. It should load the selected YAML,
    print normalized non-secret settings, and fail if a required field is
    missing.
11. Add `api_tests/run_state.py` with a session-scoped run id using the format
    `ba-api-test-<utc-timestamp>-<short-random>`.
12. Add `api_tests/auth0.py` with helpers to:
    - request an Auth0 Management API token
    - create an Auth0 database user with generated email and password
    - set or request `email_verified=true` during creation
    - fail clearly if the Management API application lacks the required scopes
    - never write generated passwords or Management API tokens to artifacts
13. Add `api_tests/browser_login.py` with a Playwright login helper. It should:
    - open `{origin}/oauth2/authorization/idp`
    - fill Auth0 Universal Login email and password fields
    - submit the form
    - wait for redirect back to the configured origin
    - read the HttpOnly `BA_SESSION` cookie from the browser context
    - verify `GET /auth/v1/user` succeeds with that cookie
14. Add `api_tests/identities.py` with a run identity model containing:
    - `primary_user`
    - `secondary_user`
    Each normal user is created through Auth0 Management API and logged in
    through Playwright.
15. Add `api_tests/auth.py` with two auth modes:
    - `browser_auth0`: provision and log in the needed identities
    - `env_cookie`: read `BA_SESSION` from the configured cookie env var for
      local debugging only
16. Add `api_tests/client.py` with a `GatewayClient` wrapper around
    `httpx.Client`. It must:
    - use `origin` as the base URL
    - attach the configured session cookie when present
    - apply the configured timeout and TLS verification mode
    - expose `api_request(method, path, **kwargs)` that prepends
      `api_base_path`
    - expose `raw_request(method, path, **kwargs)` for `/api-docs` and auth
      edge checks
17. Add `api_tests/logging.py` request/response hooks. Each request should
    append one JSON line to `artifacts/request-log.jsonl` with method, URL,
    status, elapsed time, response headers, and the first 4096 bytes of the
    response body. Redact cookies, passwords, Auth0 Management API tokens, and
    authorization headers.
18. Add `tests/api_docs/test_openapi_docs.py` with one smoke test that calls
    `raw_request("GET", openapi_path)` and asserts a JSON response containing
    top-level `openapi` and `paths`.
19. Add `README.md` with the first commands:

    ```bash
    python -m pip install -e ".[dev]"
    python -m playwright install chromium
    pytest --env local --collect-only
    pytest --env local tests/api_docs
    ```

Acceptance checks:

- `python tools/validate-environment.py --env local` succeeds.
- `pytest --env local --collect-only` succeeds without needing a live target.
- `pytest --env local tests/api_docs` fails before network access with a clear
  missing Auth0 prerequisite message when `auth.mode: browser_auth0` secrets
  are absent.
- With Auth0 prerequisites present and local Tilt running, `pytest --env local
  tests/api_docs` creates per-run normal users, logs in through Auth0, captures
  `BA_SESSION`, and downloads `/api-docs/openapi.json`.
- `artifacts/request-log.jsonl` is created during a live test run.

### Phase 2: OpenAPI Coverage Harness

Goal: make the OpenAPI contract the executable coverage manifest before adding
the endpoint suite.

Steps:

1. Copy the current unified OpenAPI snapshot from
   `../orchestration/docs-aggregator/openapi.json` into
   `schemas/openapi.json`. Use a relative path only.
2. Add `schemas/deferred-admin-operations.yaml` with the exact deferred
   admin/global operations from `OpenAPI Coverage Model`.
3. Add `api_tests/openapi.py` with:
   - `load_snapshot(path)`
   - `fetch_live_openapi(client, openapi_path)`
   - `iter_operations(openapi_doc)`
   - `operation_response_schema(openapi_doc, operation_id, status)`
4. `iter_operations` must inspect every `paths` entry and only the HTTP
   methods `get`, `post`, `put`, `patch`, and `delete`.
5. For each operation, require `operationId`, method, and path. Fail with the
   method and path when an operation has no `operationId`.
6. Add `api_tests/coverage.py` with a coverage model containing operation id,
   method, path, source (`live` or `snapshot`), deferred-admin flag, marker
   count, placeholder marker count, and final status (`covered`,
   `placeholder`, `missing`, or `deferred_admin`).
7. In `tests/conftest.py`, collect every `@pytest.mark.openapi("<id>")`
   marker during pytest collection and store the ids on the pytest config
   object.
8. Add `tests/contract/test_openapi_coverage.py`. It should load the live
   OpenAPI document when a live client is available, otherwise load
   `schemas/openapi.json`. It should fail when any non-deferred operation is
   missing a marker.
9. Add `tools/check-openapi-coverage.py --env <name> --fail-missing`. It
   should run the same coverage logic outside pytest for CI and write
   `artifacts/openapi-coverage.json`.
10. Add `tools/export-openapi-coverage.py`. It should write a JSON array of
   all operations with method, path, expected test markers, and status. It
   may also accept `--fail-missing` and `--fail-placeholder` for compatibility
   with older workflow commands, but `check-openapi-coverage.py` is the
   preferred CI gate.
11. Add `tools/refresh-openapi.py --env <name>`. It should fetch the live
    OpenAPI document and overwrite `schemas/openapi.json` only when explicitly
    run by a developer.
12. Add unit tests for `iter_operations`, duplicate marker handling, placeholder
    handling, and missing-operation failure using in-memory fake OpenAPI docs.
13. Add placeholder test files for each non-deferred operation group with
    marker-only
    skipped tests. Use the exact operation ids from `Current Public Operation
    Inventory`; each placeholder should include both
    `@pytest.mark.openapi("<operationId>")` and `@pytest.mark.placeholder`,
    then call `pytest.skip("not implemented")`.

Acceptance checks:

- `pytest --env local tests/contract/test_openapi_coverage.py` reports
  non-deferred operations as covered or placeholder and reports the deferred
  admin/global operations as `deferred_admin`.
- Adding a fake path with an `operationId` to `schemas/openapi.json` makes the
  coverage test fail until a marker is added or the operation is explicitly
  added to `schemas/deferred-admin-operations.yaml`.
- `python tools/export-openapi-coverage.py --env local` writes
  `artifacts/openapi-coverage.json`.
- The coverage artifact includes operation id, method, path, marker count,
  placeholder marker count, deferred-admin flag, and status.

### Phase 3: Read-Only Smoke And Contract Tests

Goal: prove the deployed public API lane works safely in every environment,
including production.

Steps:

1. Replace marker-only placeholders with real read-only tests first. Keep
   mutating operation placeholders skipped until Phase 5.
2. For local and staging, run read-only tests with the primary per-run normal
   user created by `browser_auth0`.
3. For production, do not create a per-run Auth0 user because first login would
   create or sync an app user. Use either `auth.mode: env_cookie` with a
   pre-created production smoke session or a stable pre-provisioned production
   smoke identity.
4. Add `api_tests/schemas.py` with `assert_response_matches_openapi(response,
   operation_id, expected_status)`. It should resolve the response schema from
   the loaded OpenAPI document and validate JSON responses with `jsonschema`.
5. Validate only documented JSON `2xx` responses in this phase. Do not invent
   schemas for undocumented error bodies.
6. Add `tests/auth/test_edge_auth.py`:
   - create a client without cookies
   - call `GET /api/v1/currencies`
   - assert `401`
   - assert the response is not HTML from the SPA
7. Add `tests/contract/test_unknown_routes.py`:
   - call `GET /api/v1/__openapi_black_box_missing__`
   - assert the route fails closed with `404` or another documented API error
   - assert the response is not the frontend HTML shell
8. Add `tests/api_docs/test_openapi_docs.py` assertions that
   `/api-docs/openapi.json` contains every operation currently listed in this
   plan.
9. Add `tests/currencies/test_currencies_readonly.py`:
   - mark `getAll`
   - call `GET /v1/currencies` with `enabledOnly=false`
   - call `GET /v1/currencies` with `enabledOnly=true`
   - if the list contains an id, mark `getById` and call
     `GET /v1/currencies/{id}`
   - if no id exists, keep the `getById` marker and skip with a clear reason
   - call `GET /v1/currencies/999999999` and assert `404`
10. Add `tests/exchange_rates/test_exchange_rates_readonly.py`:
   - mark `getExchangeRates`
   - call `GET /v1/exchange-rates` with `targetCurrency=USD`
   - also call it with `startDate` and `endDate` for a narrow historical range
   - assert documented `200`, `400`, or `422` behavior based on the current
     OpenAPI contract and environment data availability
11. Add `tests/transactions/test_transactions_readonly.py`:
   - mark `getTransactions`, `searchTransactions`, `countTransactions`, and
     `getTransaction`
   - call list, search, and count with `page=0` and `size=10`
   - if the list contains an id, call `GET /v1/transactions/{id}`
   - call an unlikely id and assert `404`
12. Add `tests/statement_formats/test_statement_formats_readonly.py`:
    - mark `listFormats` and `getFormat`
    - call `GET /v1/statement-formats?includeHidden=false`
    - call `GET /v1/statement-formats?includeHidden=true`
    - fetch one returned id when available
    - call an unlikely id and assert `404`
13. Add `tests/views/test_views_readonly.py`:
    - mark `listViews`, `getView`, and `getViewTransactions`
    - call `GET /v1/views`
    - fetch one returned id when available
    - call `GET /v1/views/{id}/transactions` for that view
    - use an unlikely id for `404` checks
14. Update the request log formatter so failed assertions print method, URL,
    status code, response headers, and clipped response body.
15. Add a pytest selector for production-safe tests. The simplest path is a
    `production_safe` marker plus a README command:

    ```bash
    pytest --env production -m "readonly and production_safe"
    ```

Acceptance checks:

- `pytest --env local -m readonly` creates a per-run normal user, logs in
  through Auth0, and runs without creating, updating, hiding, deleting,
  importing, deactivating, or bulk-deleting API resources.
- `pytest --env production -m "readonly and production_safe"` selects only
  read-only tests and does not create per-run Auth0 users.
- An unauthenticated client produces an edge-auth failure, not a direct service
  request.
- A failed read-only test includes enough request detail to replay the request
  manually.

### Phase 4: Public-API Data Builders

Goal: give mutating tests reusable public-API setup primitives without adding
cleanup logic.

Steps:

1. Add a guard fixture named `require_mutation_allowed`. It should skip tests
   marked `mutation` unless `allow_mutation: true`.
2. Add a guard fixture named `require_destructive_allowed`. It should skip
   tests marked `destructive` unless both `allow_mutation: true` and
   `allow_destructive: true`.
3. Add `api_tests/builders/statement_formats.py`:
   - build a CSV `CreateStatementFormatRequest` using required fields
     `displayName`, `formatType`, `bankName`, and `defaultCurrencyIsoCode`
   - use `formatType: CSV`, `scope: USER`, and names containing `run_id`
   - create with `POST /v1/statement-formats`
   - return the created id for later assertions
   - do not register cleanup
4. Add `api_tests/builders/transactions.py`:
   - create transaction data through `POST /v1/transactions/preview` followed
     by `POST /v1/transactions/batch`
   - use `BatchImportTransactionRequest` fields `date`, `description`,
     `amount`, `type`, `bankName`, `currencyIsoCode`, `accountId`, and
     `allowDuplicate`
   - set `description`, `bankName`, and `accountId` values containing `run_id`
   - return imported ids for tests that need them
   - do not register cleanup
5. Add `api_tests/builders/views.py`:
   - build `CreateSavedViewRequest` with `name`, `criteria`, and `openEnded`
   - use criteria that matches the transaction builder data, such as
     `accountIds`, `bankNames`, `currencyIsoCodes`, and date range
   - create with `POST /v1/views`
   - return the created id for later assertions
   - do not register cleanup
6. Add `fixtures/statements/basic.csv` with a small deterministic statement:
    headers `Date,Description,Amount,Type,Category`, one debit row, and one
    credit row.
7. Add `fixtures/statements/basic.pdf` as a deterministic checked-in PDF
   fixture with a small table the PDF wizard can parse. Do not generate
   certificates or rely on environment-specific files.
8. Add builder unit tests using `respx` for request composition. These unit
    tests should not call the real API.
9. Add a no-cleanup smoke test that creates one statement format and one saved
    view in a mutation-enabled environment, then verifies both are visible to
    the same run user and not visible to the secondary run user unless the
    public contract grants cross-user access.

Acceptance checks:

- `pytest --env local -m mutation tests/statement_formats` can create a
  test-owned statement format under the per-run user.
- Rerunning user-owned mutation tests does not collide with previous runs
  because each run uses fresh Auth0 users and run-id-prefixed data.
- No builder uses direct database, Redis, RabbitMQ, Kubernetes, or service DNS
  access.

### Phase 5: Mutating Endpoint Coverage

Goal: replace the remaining non-user placeholders with real happy-path and
validation tests.

Steps:

1. For every test in this phase, add `@pytest.mark.mutation`. Add
   `@pytest.mark.destructive` to delete, bulk-delete, import, and hide/unhide
   flows when they can alter existing user-visible state.
2. Use the request schema names from the OpenAPI snapshot when building
   payloads. Do not copy payload fields from service code.
3. Add transaction tests in `tests/transactions/test_transactions_mutation.py`:
   - `previewTransactions`: upload `fixtures/statements/basic.csv` as
     multipart `file`; assert documented preview shape
   - `batchImportTransactions`: import previewed or deterministic transaction
     rows; assert accepted/rejected row counts
   - `getTransaction`: fetch one imported id
   - `updateTransaction`: send `TransactionUpdateRequest` with a new
     `description` and `accountId`
   - `deleteTransaction`: delete one imported id and verify `404`
   - `bulkDeleteTransactions`: import two rows, send `BulkDeleteRequest.ids`,
     and verify both are gone
4. Add transaction validation tests:
   - invalid preview multipart with no file returns documented validation
     status
   - `BatchImportRequest.transactions=[]` returns documented validation status
   - `BulkDeleteRequest.ids=[]` returns documented validation status
   - malformed JSON on a JSON endpoint returns the documented `400` shape
5. Add statement-format tests in
   `tests/statement_formats/test_statement_formats_mutation.py`:
   - `createFormat`: send `CreateStatementFormatRequest`
   - `updateFormat`: send `UpdateStatementFormatRequest`
   - `hideFormat`: hide the created format and confirm default listing omits it
   - `unhideFormat`: unhide the same format and confirm default listing can see
     it again
6. Add CSV wizard tests in
   `tests/statement_formats/test_csv_wizard_mutation.py`:
   - `analyzeCsvSample`: upload `fixtures/statements/basic.csv`
   - `previewCsvMapping`: send multipart `file` plus a JSON `request` part
     matching `CsvWizardMappingPreviewRequest`
   - `saveCsvWizardFormat`: send multipart `file` plus a JSON `request` part
     matching `CsvWizardSaveRequest`; assert the returned user-scoped format is
     visible to the primary run user
7. Use this minimum CSV mapping unless OpenAPI changes:
   - `amountMode: SINGLE_AMOUNT_WITH_TYPE`
   - `dateColumn: Date`
   - `dateFormat: MM/dd/uuuu`
   - `descriptionColumn: Description`
   - `amountColumn: Amount`
   - `typeColumn: Type`
   - `categoryColumn: Category`
8. Add PDF wizard tests in
   `tests/statement_formats/test_pdf_wizard_mutation.py` using
   `fixtures/statements/basic.pdf`:
   - `analyzePdfSample`
   - `previewPdfMapping` with `PdfWizardMappingPreviewRequest`
   - `savePdfWizardFormat` with `PdfWizardSaveRequest`
9. Add saved-view tests in `tests/views/test_views_mutation.py`:
   - create transactions with the transaction builder
   - `createView`: create a view whose criteria match the builder data
   - `updateView`: update name, criteria, and `openEnded`
   - `getViewTransactions`: verify matching transactions are returned
   - `pinTransaction` and `unpinTransaction`: pin and unpin one transaction
   - `bulkPinTransactions`: pin two transactions with
     `BulkViewTransactionRequest.ids`
   - `excludeTransaction` and `unexcludeTransaction`: exclude and unexclude one
     transaction
   - `bulkExcludeTransactions`: exclude two transactions with
     `BulkViewTransactionRequest.ids`
   - `deleteView`: delete the view and verify `404`
10. For every JSON request-body schema used above, add at least one validation
    test that removes a required field or sends an invalid enum value.
11. Remove placeholder skips for all implemented non-admin operation ids.

Acceptance checks:

- `pytest --env local -m mutation` passes in a mutation-enabled local
  environment with Auth0 and PDF fixture prerequisites satisfied.
- `pytest --env production -m mutation --collect-only` may collect tests, but
  executing them skips before making any mutating request.
- Coverage shows every non-admin operation as `covered`.
- Request logs prove mutating tests used `{origin}{api_base_path}` URLs, not
  service DNS.

### Phase 6: Non-Admin Authorization Coverage

Goal: test user-owned resource isolation without admin credentials or admin
endpoints.

Steps:

1. Use only the primary and secondary per-run normal users from
   `api_tests/identities.py`.
2. Add `tests/authorization/test_cross_user_resources.py`:
   - mark tests with `@pytest.mark.authorization`
   - primary user creates transactions and saved views through public APIs
   - secondary user attempts direct id access to those resources
   - assert the documented `403` or `404` isolation behavior
   - do not call `/v1/users`, `/v1/transactions/search/count`,
     `/v1/currencies` write endpoints, or `/v1/exchange-rates/import`
3. Add negative tests that a normal user cannot use another user's transaction
   id in saved-view pin, exclude, bulk pin, or bulk exclude operations.
4. Keep these tests scoped to normal generated users. Do not add admin
   credentials, admin login, promotion, or cleanup logic in this phase.

Acceptance checks:

- `pytest --env staging -m authorization` creates generated normal users, logs
  them in through Auth0, and verifies documented cross-user isolation for
  user-owned resources.
- The authorization suite makes no admin endpoint requests.
- No authorization test relies on direct database or service-internal setup.

### Deferred Admin And Global Scope

Admin/global coverage is intentionally deferred and is not part of the initial
staging gate.

Deferred operations:

- `countTransactionsAcrossUsers`
- `create` (`POST /v1/currencies`)
- `update` (`PUT /v1/currencies/{id}`)
- `importLatestExchangeRates`
- `getUsers`
- `getUser`
- `deactivateUser`

Before implementing those operations, create a separate detailed plan that
settles:

- admin identity provisioning or promotion
- whether admin test runs create fresh admin users or use a stable admin
- cleanup for generated app users and Auth0 users
- cleanup or reset strategy for global currency catalog state
- exchange-rate import determinism and external-provider blast radius
- production behavior, which should remain read-only by default

### Phase 7: CI And Self-Hosted Runner Integration

Goal: make the standalone suite usable as a deployment gate.

Steps:

1. Add `.github/workflows/ci.yml` for repository-local quality checks:
   - install Python
   - install `.[dev]`
   - run `python -m playwright install chromium`
   - run `ruff check .`
   - run `mypy api_tests`
   - run harness unit tests that do not require a live API
   - run `pytest --env local --collect-only`
2. Add `.github/workflows/api-tests.yml` with `workflow_dispatch` inputs:
   `environment`, `marker_expression`, `target_origin`, `verify_tls`, and
   `allow_destructive`.
3. In the workflow, copy `target_origin` into the selected environment config
   at runtime or expose it through an environment-variable override supported
   by `api_tests/config.py`.
4. Store Auth0 Management API credentials as GitHub Actions secrets. Do not
   write generated user passwords, session cookies, or Management API tokens
   into environment YAML or artifacts.
5. Run read-only production checks only with:

   ```bash
   pytest --env production -m "readonly and production_safe" \
     --junitxml artifacts/junit.xml
   ```

6. Run full staging checks on the self-hosted runner label from the staging
   plan:

   ```bash
   pytest --env staging --junitxml artifacts/junit.xml
   python tools/check-openapi-coverage.py --env staging \
     --fail-missing --fail-placeholder
   ```

7. Upload these artifacts on every workflow run:
   `artifacts/junit.xml`, `artifacts/openapi-coverage.json`,
   `artifacts/request-log.jsonl`, and Playwright traces/screenshots when
   present.
8. Add a workflow summary step that prints operation coverage counts:
   covered, placeholder, and missing.
9. In the orchestration staging workflow from
   `docs/plans/self-hosted-staging-runner-cd-plan.md`, call this repository
   after deployment verification and before promotion approval.
10. Make the staging gate fail on any missing non-deferred operation,
    placeholder non-deferred operation, failed test, failed Auth0 login, or
    missing prerequisite.
11. Keep production workflow defaults read-only. Require a manual
    `allow_destructive=true` input and environment protection before any
    production mutation can run.

Acceptance checks:

- CI quality checks pass without a live Budget Analyzer target.
- A manual staging workflow run executes API tests on `pn50-staging` after a
  deployment.
- Failed API tests block staging promotion.
- Artifacts are available from the workflow run and include enough data to
  identify the failed operation and request.
- Production workflow defaults cannot run mutating or destructive tests.

## Staging Runner Integration Point

The self-hosted staging workflow should call this repository after deployment:

```bash
pytest --env staging --junitxml artifacts/junit.xml
python tools/check-openapi-coverage.py --env staging \
  --fail-missing --fail-placeholder
```

The staging workflow should provide:

- target origin
- trusted CA or TLS verification mode
- Auth0 Management API credentials for generated normal users
- Auth0 database connection name for generated users
- configured test email domain
- `allow_mutation=true`
- `allow_destructive=true` only for ephemeral staging
- deployment metadata such as image manifest or release id

## Maintenance Rules

- Regenerate the OpenAPI snapshot when the unified API contract changes.
- Treat every new non-deferred public operation as incomplete until it has a
  real test. If a new operation is admin/global, add it to
  `schemas/deferred-admin-operations.yaml` and the deferred-admin planning
  scope.
- Keep detailed endpoint behavior in OpenAPI and service-local docs, not in
  this plan.
- Keep environment secrets out of checked-in YAML.
- Do not add direct database, Redis, RabbitMQ, or Kubernetes setup paths to the
  black-box API tests.
- Do not broaden production test permissions just to exercise mutating
  endpoints.
- Do not add generic cleanup callbacks or janitors in the initial
  implementation. User-owned test residue stays scoped under per-run generated
  users.
- Keep admin/global operations in `schemas/deferred-admin-operations.yaml`
  until a separate admin cleanup and safety plan exists.

## Resolved Decisions

1. Repository shape: create a standalone `budget-analyzer-api-tests`
   repository. It can be public because secrets stay in environment variables
   and GitHub Actions secrets.
2. Auth strategy: `browser_auth0` is the primary mode. `env_cookie` exists only
   for local debugging.
3. Normal identities: every local or staging full test run creates fresh Auth0
   normal users and logs them in through the real browser flow.
4. Admin/global functionality: defer user administration, cross-user aggregate
   count, currency catalog mutation, and exchange-rate import until a separate
   cleanup and safety plan exists.
5. Cleanup: do not implement cleanup callbacks, janitors, or Auth0 user
   deletion in the initial implementation. The per-run generated user is the
   data boundary for user-owned data.
6. Production: production checks are read-only and must not create per-run
   users. Use a stable production smoke identity or a supplied `BA_SESSION`.
7. PDF wizard: include a deterministic checked-in PDF fixture and implement the
   PDF operations instead of waiving them.
8. OpenAPI source: fetch live OpenAPI at runtime and keep a checked-in
    `schemas/openapi.json` snapshot for drift detection.

## Initial Success Criteria

- Standalone repo can run one authenticated request against local Tilt by URL.
- Coverage harness discovers all 43 current unified OpenAPI operations.
- Every non-admin operation has a real mapped test before the staging gate is
  considered complete.
- Read-only tests are safe against production by default.
- Full non-admin mutating tests pass against an isolated local or staging
  environment.
- Staging runner workflow can consume the suite as a deployment gate.
