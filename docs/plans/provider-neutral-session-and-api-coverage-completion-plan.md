# Provider-Neutral Session And API Coverage Completion Plan

Complete the Budget Analyzer black-box API suite from its current implementation
state while removing Auth0 Management API provisioning from the default test
workflow. The core suite will consume opaque Budget Analyzer session cookies,
keep external identity-provider behavior behind optional session-acquisition
adapters, finish the pending behavioral coverage, and become ready for local,
staging, and production-safe execution.

Status: Ready for implementation

Supersedes:

- `docs/plans/openapi-black-box-api-test-repository-plan.md`
- `docs/plans/openapi-behavioral-coverage-remediation-plan.md`

## Current Baseline

The repository already has a typed Python 3.12 harness, public-gateway client,
environment validation, request-log redaction, OpenAPI inventory and marker
coverage, mutation policy enforcement, typed public-API payload builders,
deterministic CSV and PDF fixtures, and endpoint tests for every non-deferred
operation.

The verified non-live baseline at plan creation is:

```text
82 tests collected
42 harness, contract, auth-helper, and tool tests passed
43 OpenAPI operations inventoried
36 non-deferred operations with non-placeholder markers
7 deferred admin/global operations
9 production-safe read-only tests selected
Ruff formatting and linting passed
strict mypy passed
```

The OpenAPI schema assertion foundation and deterministic statement fixtures
from the deprecated behavioral remediation plan are implemented. Endpoint
schema application, gateway safety tests, behavioral postconditions,
validation coverage, non-admin authorization coverage, live drift
documentation, and CI integration remain incomplete.

Local and staging currently use `browser_auth0`. That mode requests an Auth0
Management API token, creates primary and secondary Auth0 database users,
drives Auth0 Universal Login through Playwright, and leaves the external users
and their application data behind. This coupling is the first issue this plan
remediates.

## Resolved Design Decisions

1. The core authentication contract is a provider-neutral session bundle. It
   contains opaque `BA_SESSION` cookies for a primary identity and, when a test
   requires it, a secondary identity.
2. Core session and identity models must not contain Auth0 user ids, Auth0
   domains, management tokens, database connection names, or provider-specific
   credentials.
3. Auth0 Management API user creation is removed. The suite does not create or
   delete external identity-provider users.
4. Local and staging automation may log in two pre-provisioned normal users
   through the public `/oauth2/authorization/idp` flow. Browser automation is
   an optional session-acquisition adapter, not a dependency of endpoint test
   code.
5. Local debugging and CI may instead supply primary and secondary opaque
   session cookies through secret environment variables.
6. Production remains read-only, accepts only a supplied primary session, and
   must not require a secondary identity, browser login, or mutation setup.
7. The API suite never seeds Redis sessions, calls service-internal identity
   hooks, exchanges identity-provider tokens directly for API access, or
   bypasses Session Gateway and ext_authz.
8. Stable pre-provisioned users replace fresh per-run users. Isolation comes
   from run-id namespacing and controlled public-API setup. Persistent
   environments may retain namespaced user-owned residue; ephemeral staging
   lifecycle or reset remains an orchestration responsibility outside this
   repository.
9. A narrow browser authentication smoke test may prove the deployed
   identity-provider integration. Failures in that smoke test must be reported
   separately from endpoint behavior failures.
10. Deferred admin/global operations remain deferred until a separate admin
    identity and global-state safety plan exists.

## Phase 1: Provider-Neutral Session Contract

### Goal

Introduce the typed provider-neutral session boundary without changing public
API request behavior.

### Scope

- Replace the Auth0-shaped durable identity model with value objects such as:
  - `SessionIdentity` containing only a logical label and session cookie
  - `SessionBundle` containing a required primary identity and optional
    secondary identity
  - `SessionContext` describing the selected acquisition mode and bundle
- Define a small session-acquisition interface or callable protocol that
  returns `SessionBundle`.
- Adapt the current `browser_auth0` and `env_cookie` implementations behind
  that interface temporarily so this phase preserves existing execution.
- Change shared pytest fixtures and `GatewayClient` construction to consume
  `SessionContext` or `SessionBundle`, not Auth0-shaped run identities.
- Keep provider-specific created-user and credential types private to the
  transitional Auth0 adapter.
- Preserve the existing `SessionCookie` secret-redaction behavior.

### Non-goals

- Do not remove Auth0 Management API code yet; Phase 2 removes it after the new
  contract is proven.
- Do not add another identity provider.
- Do not add a test-only login endpoint or direct session-storage access.
- Do not change endpoint tests beyond fixture/type compatibility.

### Required context

- `src/api_tests/config.py`
- `src/api_tests/auth.py`
- `src/api_tests/identities.py`
- `src/api_tests/browser_login.py`
- `src/api_tests/client.py`
- `src/api_tests/pytest_plugin.py`
- `tests/auth/test_phase1_auth_helpers.py`
- `environments/*.yaml`

### Implementation notes

- Keep provider-neutral types in modules that do not import an Auth0 adapter.
- Use `repr=False` for cookies and credentials.
- Do not carry email addresses or provider subject ids unless a specific
  browser adapter needs them internally.
- A missing secondary session is valid for read-only and single-user tests.
  Authorization fixtures must fail or skip with a precise prerequisite message
  when they require one.
- Continue constructing API URLs only through `GatewayClient`.

### Validation

```bash
.venv/bin/python tools/validate-environment.py --env local
.venv/bin/python tools/validate-environment.py --env staging
.venv/bin/python tools/validate-environment.py --env production
.venv/bin/python -m pytest --env local tests/auth tests/contract -q
.venv/bin/python -m mypy src tests
.venv/bin/python -m ruff check .
```

### Completion criteria

- Core identity/session types contain no Auth0-specific identifiers.
- Existing acquisition behavior is available only through the new session
  interface.
- Endpoint fixtures no longer expose Auth0-created users.
- Unit tests prove that cookies and credentials are not exposed by
  representations or normalized configuration output.
- Existing collection and marker-policy safety checks still pass.

## Phase 2: Session Acquisition Without Auth0 Management API

### Goal

Make supplied sessions and pre-provisioned browser users fully usable, then
remove Auth0 Management API provisioning from the repository.

### Scope

- Replace `AuthMode = Literal["browser_auth0", "env_cookie"]` with final
  provider-neutral modes:
  - `supplied_sessions`
  - `browser_preprovisioned`
- Add an explicit pytest `--session-mode` override for choosing one of those
  modes without editing a checked-in environment file. Do not automatically
  fall back from one mode to the other when prerequisites are missing.
- Add typed declarative configuration for environment-variable names:
  - supplied primary session
  - optional supplied secondary session
  - pre-provisioned primary username and password
  - pre-provisioned secondary username and password
- Keep secrets in environment variables. Environment YAML contains only the
  names of those variables.
- Enforce these policies in configuration validation:
  - production requires `supplied_sessions`
  - production requires read-only policy
  - production does not require a secondary session
  - staging authorization execution requires a secondary session
  - browser acquisition is forbidden for production
  - a command-line mode override cannot weaken production policy
- Implement `supplied_sessions` acquisition:
  - read a primary `BA_SESSION` value from its configured environment variable
  - read an optional secondary `BA_SESSION` value from its configured
    environment variable
  - return a provider-neutral `SessionBundle`
- Implement `browser_preprovisioned` acquisition:
  - read primary and secondary username/password pairs from configured secret
    environment variables
  - start login at `{origin}/oauth2/authorization/idp`
  - complete the hosted browser login flow
  - capture the configured HttpOnly session cookie
  - verify the session through `GET /auth/v1/user`
  - return only the resulting provider-neutral session identities
- Keep hosted-login page selectors inside the browser adapter. Endpoint tests,
  `GatewayClient`, and shared session models must not import or name that
  provider implementation.
- Add an `auth_smoke` pytest marker for an optional focused browser/session
  integration test.
- Remove:
  - `src/api_tests/auth0.py`
  - Auth0 Management API token and user-creation code
  - management domain, client id, client secret, and connection fields from
    environment configuration
  - `Auth0CreatedUser` and `auth0_user_id`
  - Management API unit tests and prerequisite messages
  - Management API dependencies in README commands and examples
- Update local and staging YAML for pre-provisioned browser users while
  retaining the explicit `--session-mode supplied_sessions` path.
- Keep production on one supplied primary session.
- Make `/api-docs` and unauthenticated gateway tests construct clients that do
  not acquire authenticated sessions.

### Non-goals

- Do not create external users through browser signup.
- Do not use password, client-credentials, or other identity-provider grants
  directly as Budget Analyzer API credentials.
- Do not generalize hosted login selectors into a speculative universal OIDC
  UI framework.
- Do not run live browser authentication from ordinary unit tests.

### Required context

- Phase 1 provider-neutral session types
- `src/api_tests/auth0.py`
- `src/api_tests/browser_login.py`
- `src/api_tests/auth.py`
- `tests/auth/test_phase1_auth_helpers.py`
- `tests/api_docs/test_openapi_docs.py`
- `README.md`

### Implementation notes

- Use explicit environment-variable names such as
  `BA_PRIMARY_SESSION`, `BA_SECONDARY_SESSION`,
  `BA_PRIMARY_TEST_USERNAME`, `BA_PRIMARY_TEST_PASSWORD`,
  `BA_SECONDARY_TEST_USERNAME`, and `BA_SECONDARY_TEST_PASSWORD`.
  The YAML should name variables rather than contain their values.
- Browser acquisition may remain aware of the deployed hosted login page.
  That awareness must end at the adapter boundary.
- A browser auth smoke should prove Session Gateway integration once per run;
  endpoint tests should reuse the acquired bundle.
- Convert missing credentials, failed hosted login, and expired sessions into
  clear prerequisite errors without printing secret values.
- Preserve request-log redaction for cookies, authorization headers,
  passwords, and token-shaped response fields.

### Validation

```bash
.venv/bin/python -m pytest --env local tests/auth tests/contract tests/tools -q
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
.venv/bin/python -m mypy src tests
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
rg -n "AUTH0_MGMT|Auth0Management|create_database_user|auth0_user_id" \
  src tests environments README.md
```

The final `rg` command must return no matches.

Live acceptance, when pre-provisioned local credentials are available:

```bash
.venv/bin/python -m pytest --env local -m auth_smoke
```

### Completion criteria

- No Auth0 Management API secret or scope is required by any repository
  command.
- No test run creates an external identity-provider user.
- Supplied primary and secondary sessions work without browser startup.
- Pre-provisioned browser acquisition produces the same provider-neutral
  session contract.
- The optional auth smoke proves the public login-to-session path.
- Public OpenAPI checks do not create or log in test users.

## Phase 3: Stable-Identity Data Isolation And Test Fixtures

### Goal

Make the endpoint suite reliable with stable primary and secondary users while
preserving public-API-only setup and environment safety.

### Scope

- Replace `auth_context` fixtures with provider-neutral `session_context` and
  `session_bundle` fixtures.
- Provide:
  - `gateway_client` for the primary session
  - `secondary_gateway_client` for the secondary session
  - an unauthenticated `GatewayClient` fixture that does not request sessions
- Ensure authorization fixtures report a clear missing-secondary prerequisite
  instead of failing with an attribute or provider-specific error.
- Audit builders and resource helpers so every created name, description,
  account id, bank name, and view criterion uses the UTC run id.
- Remove assumptions that a stable user starts with empty lists or no existing
  transactions.
- Keep assertions centered on ids and names created by the current run.
- Add unit tests for builder request composition using
  `httpx.MockTransport` or `respx`; do not call live services.
- Document the isolation policy:
  - the suite owns only resources created under the current run id
  - endpoint-specific delete/hide/unhide behavior is allowed when under test
  - no generic cleanup registry or janitor is introduced
  - persistent local environments may retain namespaced residue
  - staging reset or environment recreation is owned by orchestration
- Keep mutation and destructive marker skips at collection time, before
  network-capable fixtures initialize.

### Non-goals

- Do not directly reset databases, Redis, RabbitMQ, Kubernetes resources, or
  identity-provider users.
- Do not delete or mutate resources discovered from a general list unless the
  current run created them.
- Do not implement admin/global cleanup.

### Required context

- `src/api_tests/pytest_plugin.py`
- `src/api_tests/resources.py`
- `src/api_tests/builders/`
- `src/api_tests/run_state.py`
- `tests/conftest.py`
- endpoint test modules

### Implementation notes

- Prefer explicit created-resource value objects over global registries.
- Builder unit tests should verify method, public API path, params, multipart
  fields, and JSON bodies.
- Keep generated values short enough for documented API field limits while
  preserving a recognizable run-id prefix.
- If the OpenAPI contract does not provide a public cleanup operation, retain
  the namespaced residue rather than bypassing the API.

### Validation

```bash
.venv/bin/python -m pytest --env local tests/auth tests/contract tests/tools -q
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
.venv/bin/python -m mypy src tests
```

### Completion criteria

- All endpoint clients derive from the provider-neutral session bundle.
- Secondary-user access is available without provider-specific identity
  fields.
- Generated setup is unique under stable users and makes no empty-account
  assumptions.
- Builder composition has deterministic non-live tests.
- Mutation and destructive tests remain blocked before client setup when
  environment policy disables them.

## Phase 4: Apply OpenAPI Schema Assertions

### Goal

Validate every documented successful JSON endpoint response against the
checked-in OpenAPI contract.

### Scope

- Add `assert_response_matches_openapi` to every endpoint test receiving a
  documented JSON `2xx` response.
- Add or reuse an `openapi_snapshot` fixture in endpoint tests.
- Run schema validation before local field and behavioral assertions.
- Cover read-only production-safe tests first, then mutating tests.
- For `204` responses, assert no content rather than attempting JSON
  validation.
- Validate the `/api-docs/openapi.json` smoke response as an OpenAPI document
  shape, but do not incorrectly resolve it as one of the `/api` operation
  schemas.
- Split happy-path and expected-error cases where a single test currently
  accepts both success and a business error.

### Non-goals

- Do not invent schemas for undocumented error responses.
- Do not silently skip schema validation when a documented successful JSON
  response lacks a schema.
- Do not fetch live OpenAPI during ordinary collection or offline tests.

### Required context

- `src/api_tests/schemas.py`
- `src/api_tests/openapi.py`
- `tests/contract/test_schema_assertions.py`
- all endpoint test modules

### Implementation notes

- Keep assertion messages naming operation id, status, response path, and
  schema path.
- If applying the helper exposes a defective OpenAPI schema or API response,
  report the contract defect. Do not weaken the test to accommodate it.
- Preserve exact OpenAPI operation ids.

### Validation

```bash
.venv/bin/python -m pytest --env local tests/contract/test_schema_assertions.py -q
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
.venv/bin/python -m mypy src tests
rg -L "assert_response_matches_openapi" \
  tests/currencies/test_*.py \
  tests/exchange_rates/test_*.py \
  tests/statement_formats/test_*.py \
  tests/transactions/test_*.py \
  tests/views/test_*.py
```

Review any `rg -L` result and retain it only when the file has no documented
JSON `2xx` response to validate.

### Completion criteria

- Every documented successful JSON endpoint response is schema-validated.
- `204` responses assert an empty body.
- Happy-path tests no longer accept a business error as an equivalent outcome.
- Offline collection and production-safe selection remain independent of a
  live target.

## Phase 5: Gateway Safety And Behavioral Postconditions

### Goal

Prove the public gateway boundary and observable endpoint effects rather than
status codes alone.

### Scope

- Add `tests/auth/test_edge_auth.py` using an unauthenticated client:
  - call `GET /v1/currencies` through `api_request`
  - assert the exact deployed unauthenticated status expected by the gateway
  - assert the response is not the SPA HTML shell
  - do not acquire primary or secondary sessions
- Add `tests/contract/test_unknown_routes.py`:
  - call `/v1/__openapi_black_box_missing__`
  - assert fail-closed API behavior
  - assert non-HTML content
- Complete endpoint postconditions:
  - transaction delete is followed by `GET` returning documented `404`
  - bulk transaction delete uses two generated ids and verifies both gone
  - batch import count equals controlled input count
  - preview returns the expected controlled row values
  - statement format update is fetched and verified
  - hide removes the created format from default listing
  - unhide restores it
  - unknown statement format id produces documented `404`
  - saved-view delete is followed by documented `404`
  - matching transactions appear in the expected view membership group
  - pin/unpin and exclude/unexclude are verified through a subsequent fetch
  - bulk pin/exclude use two ids and assert exact counts
- Add safe unknown-id checks to read-only tests when they require no mutation.
- Improve shared assertion diagnostics only where repeated behavior justifies
  a small helper.

### Non-goals

- Do not use broad status ranges for behavior that should succeed.
- Do not inspect databases or service logs to prove postconditions.
- Do not add production-safe markers to tests requiring generated data.

### Required context

- endpoint tests and public resource helpers
- `src/api_tests/assertions.py`
- `src/api_tests/client.py`
- OpenAPI documented statuses

### Implementation notes

- Verify behavior with follow-up public API requests.
- Assert exact counts when the input set is controlled.
- If the public API is eventually consistent, use a bounded polling helper
  based on the configured eventual timeout; do not add arbitrary sleeps.
- Keep request-log output redacted.

### Validation

```bash
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
.venv/bin/python -m mypy src tests
```

Live acceptance:

```bash
.venv/bin/python -m pytest --env local \
  tests/auth/test_edge_auth.py \
  tests/contract/test_unknown_routes.py
.venv/bin/python -m pytest --env local \
  tests/transactions tests/statement_formats tests/views
```

### Completion criteria

- Gateway unauthenticated and unknown-route behavior is covered without
  session provisioning.
- Delete, hide/unhide, pin/unpin, exclude/unexclude, and bulk operations prove
  their observable postconditions.
- Controlled bulk operations use at least two resources and assert exact
  results.
- Happy-path tests require successful behavior.

## Phase 6: Validation And Negative Paths

### Goal

Cover representative documented input failures without coupling assertions to
service implementation details.

### Scope

- Add a helper that obtains the documented validation statuses for an
  operation and asserts stable error fields only when OpenAPI documents them.
- Transactions:
  - preview without a multipart file
  - batch import with an empty transaction list
  - bulk delete with an empty id list
  - malformed JSON on a JSON endpoint
  - update with an invalid field value documented by the request schema
- Statement formats:
  - create missing a required field
  - update with an invalid enum or payload shape
  - CSV preview/save without the JSON request part
  - PDF preview/save without the JSON request part
  - retain separate invalid-file PDF tests
- Saved views:
  - create missing `name` or `criteria`
  - update with invalid criteria
  - bulk pin and exclude with empty id lists
- Mark each negative test according to the request and setup it performs.
- Keep production-safe markers off tests requiring generated data or mutation.

### Non-goals

- Do not assert undocumented exception messages, Java class names, stack
  traces, or service-internal error codes.
- Do not broaden production permissions.
- Do not allow negative tests to be the only marker coverage for a successful
  operation.

### Required context

- OpenAPI request and response schemas
- `src/api_tests/assertions.py`
- domain endpoint test modules
- marker safety policy

### Implementation notes

- Derive request fields and valid/invalid enum values from OpenAPI, not service
  source code.
- Keep malformed JSON requests explicit by passing raw content and content
  type through `GatewayClient`.
- When the contract documents multiple validation statuses, assert only the
  set the contract actually permits and record discrepancies as contract
  defects.

### Validation

```bash
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
.venv/bin/python -m mypy src tests
```

Live acceptance:

```bash
.venv/bin/python -m pytest --env local -m "mutation or destructive"
```

### Completion criteria

- Representative JSON and multipart validation failures are covered.
- Negative assertions use documented statuses and stable contract fields.
- Happy-path operation markers remain present.
- Production-safe selection contains no new mutating setup.

## Phase 7: Non-Admin Authorization Coverage

### Goal

Verify user-owned resource isolation with stable primary and secondary normal
sessions.

### Scope

- Add `tests/authorization/test_cross_user_resources.py`.
- Use only public API setup through the primary and secondary
  `GatewayClient` instances.
- Cover at least:
  - secondary cannot fetch the primary transaction
  - secondary cannot fetch the primary saved view
  - secondary cannot fetch the primary statement format when user ownership
    applies
  - secondary cannot mutate primary view pin/exclude state
  - secondary cannot add a primary transaction to a secondary view by pin,
    exclude, bulk pin, or bulk exclude
- Assert the documented `403` or `404` behavior for each resource boundary.
- Mark tests with `authorization` and with mutation/destructive markers
  appropriate to their public setup.
- Add a clear prerequisite result when a secondary session is absent.
- Keep production read-only selection independent of secondary credentials.

### Non-goals

- Do not call deferred admin/global endpoints.
- Do not promote users, inspect roles in Auth0, or introduce admin secrets.
- Do not identify ownership by querying internal persistence.
- Do not create new external users.

### Required context

- Phase 3 secondary client fixture
- public transaction, statement-format, and saved-view builders
- OpenAPI authorization/error responses
- `schemas/deferred-admin-operations.yaml`

### Implementation notes

- Create all resources under the current run id.
- Treat non-disclosure `404` as distinct from missing authentication `401`.
- Test both direct resource access and relationship mutation because those may
  have different authorization checks.
- Keep the primary and secondary logical labels provider-neutral.

### Validation

```bash
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
.venv/bin/python -m mypy src tests
```

Live acceptance with two pre-provisioned staging sessions:

```bash
.venv/bin/python -m pytest --env staging -m authorization
```

### Completion criteria

- Cross-user isolation is covered for transactions, views, and applicable
  statement formats.
- Relationship mutation cannot cross user boundaries.
- Authorization tests make no admin/global requests.
- Missing secondary credentials produce a precise prerequisite result.
- No test creates or deletes identity-provider users.

## Phase 8: Live OpenAPI Drift And Tool Reliability

### Goal

Make staging coverage compare the deployed public contract with the checked-in
snapshot while keeping ordinary development offline.

### Scope

- Keep snapshot coverage as the default.
- Document and test the live command:

  ```bash
  .venv/bin/python tools/check-openapi-coverage.py \
    --env staging \
    --source live \
    --fail-missing \
    --fail-placeholder
  ```

- Add `--compare-snapshot` to the coverage checker.
- Report:
  - operations present live but absent from the snapshot
  - operations present in the snapshot but absent live
  - method changes for the same operation id
  - path changes for the same operation id
- Give drift output a stable JSON representation in the coverage artifact or
  a separate reviewable artifact.
- Catch expected `httpx` connection, TLS, timeout, and invalid-JSON failures in
  coverage and refresh tools. Return documented non-zero exit codes without
  tracebacks.
- Ensure live OpenAPI fetch does not acquire authenticated sessions.
- Add tool unit tests using `httpx.MockTransport`; do not call a live target
  from unit tests.

### Non-goals

- Do not refresh the checked-in snapshot automatically.
- Do not make collection depend on staging.
- Do not authenticate to fetch public OpenAPI unless the product contract
  intentionally changes and is documented first.

### Required context

- `src/api_tests/tools/openapi_coverage.py`
- `src/api_tests/openapi.py`
- coverage tool entrypoints
- `tests/tools/test_openapi_coverage_tools.py`
- `schemas/openapi.json`

### Implementation notes

- Match drift by exact operation id, then compare method and path.
- Keep intentional snapshot refresh behind `tools/refresh-openapi.py`.
- Error output should name the environment, URL/path, and failure category
  without disclosing cookies or credentials.

### Validation

```bash
.venv/bin/python -m pytest --env local tests/tools tests/contract/test_openapi_coverage.py -q
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
.venv/bin/python tools/export-openapi-coverage.py --env local
.venv/bin/python -m mypy src tests
```

Live acceptance:

```bash
.venv/bin/python tools/check-openapi-coverage.py \
  --env staging \
  --source live \
  --compare-snapshot \
  --fail-missing \
  --fail-placeholder
```

### Completion criteria

- Live coverage checks the deployed `/api-docs/openapi.json`.
- Drift is explicit and machine-readable.
- Snapshot-only commands remain offline.
- Expected network and configuration failures return concise tool errors.
- Live OpenAPI checks require no identity-provider or session credentials.

## Phase 9: Documentation And Operator Workflow

### Goal

Synchronize repository documentation with provider-neutral sessions, stable
test identities, and the completed behavioral suite.

### Scope

- Update `README.md` with:
  - offline setup and quality gates
  - supplied primary/secondary session usage
  - pre-provisioned browser credential usage
  - optional auth smoke execution
  - local and staging full-suite commands
  - production read-only command
  - snapshot and live OpenAPI coverage commands
- Update `AGENTS.md` to remove Auth0 Management API requirements and describe
  the new session boundary.
- Add focused operational documentation under `docs/` when README would
  become too detailed:
  - session acquisition modes and prerequisites
  - stable-user data isolation
  - marker conventions for validation and authorization tests
  - live drift artifacts and exit behavior
- State clearly that hosted browser login remains an optional external
  integration test.
- Document that persistent user-owned residue is run-id namespaced and that
  ephemeral staging reset belongs to orchestration.
- Keep both deprecated plans intact except for their deprecation notices and
  successor links.

### Non-goals

- Do not document secret values.
- Do not claim support for identity providers that have not been exercised.
- Do not mark admin/global operations complete.
- Do not rewrite historical plan results.

### Required context

- all behavior and commands implemented in Phases 1 through 8
- `README.md`
- `AGENTS.md`
- environment YAML
- tool help output

### Implementation notes

- Use provider-neutral language for the core suite.
- Name the deployed hosted-login adapter honestly where it remains
  provider-specific.
- Keep examples safe to copy: use placeholder secret values and read-only
  production selectors.
- Document prerequisite failures as expected stop conditions, not harness
  defects.

### Validation

```bash
.venv/bin/python tools/validate-environment.py --env local
.venv/bin/python tools/validate-environment.py --env staging
.venv/bin/python tools/validate-environment.py --env production
.venv/bin/python tools/check-openapi-coverage.py --help
.venv/bin/python -m pytest --help
rg -n "AUTH0_MGMT|create:users|create fresh Auth0" \
  README.md AGENTS.md environments src tests docs \
  --glob '!docs/plans/**'
```

The final `rg` command must return no active guidance or implementation
references.

### Completion criteria

- Setup and execution documentation matches actual commands.
- Operators can distinguish supplied sessions, optional browser auth smoke,
  offline coverage, live coverage, and production-safe execution.
- Active documentation no longer requires Auth0 Management API access.
- Historical plans are clearly deprecated and point here.

## Phase 10: CI Workflows And Final Validation

### Goal

Make the completed provider-neutral suite consumable as a deployment gate
without reintroducing identity-provider management credentials.

### Scope

- Add a repository quality workflow that:
  - installs Python 3.12 and `.[dev]`
  - runs Ruff format check and lint
  - runs strict mypy
  - runs non-live harness tests
  - runs local collection
  - runs snapshot OpenAPI marker coverage
- Add a manually dispatchable API test workflow with inputs for environment,
  marker expression, target origin override, TLS policy, session acquisition
  mode, and destructive policy where allowed.
- Configure staging secrets for either:
  - primary and secondary supplied sessions; or
  - primary and secondary pre-provisioned browser credentials
- Do not store Auth0 Management API client credentials.
- Run the optional auth smoke separately so its result is distinguishable from
  endpoint behavior.
- Run live OpenAPI drift coverage for staging.
- Keep production invocation fixed to:

  ```bash
  pytest --env production -m "readonly and production_safe"
  ```

- Upload JUnit, OpenAPI coverage/drift, redacted request log, and browser
  traces/screenshots when present.
- Document the handoff command for a future orchestration self-hosted staging
  workflow without modifying orchestration from this repository.
- Run the full final non-live gate.
- Run live endpoint, authorization, auth smoke, and drift checks before
  changing this plan status to complete.

### Non-goals

- Do not enable production mutation or destructive execution.
- Do not add direct staging database or identity-provider cleanup.
- Do not modify orchestration workflows from this repository.
- Do not upload cookies, passwords, authorization headers, or browser storage
  state as artifacts.

### Required context

- completed Phases 1 through 9
- `.github/workflows/`
- repository secret and artifact policy
- `README.md`
- production environment safety validation

### Implementation notes

- Prefer supplied sessions when the external workflow can renew them securely.
  Use the browser adapter when the staging gate must acquire sessions itself.
- Ensure browser traces and screenshots cannot expose typed passwords or
  session storage before uploading them.
- Make mutation/destructive permissions explicit inputs and reject them for
  production regardless of workflow input.
- Keep the quality workflow entirely non-live.

### Validation

Final non-live gate:

```bash
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
.venv/bin/python -m pytest --env local tests/auth tests/contract tests/tools
.venv/bin/python -m pytest --env production -m "readonly and production_safe" --collect-only
```

Required live validation:

```bash
.venv/bin/python -m pytest --env local -m readonly
.venv/bin/python -m pytest --env local -m "mutation or destructive"
.venv/bin/python -m pytest --env staging -m authorization
.venv/bin/python -m pytest --env staging -m auth_smoke
.venv/bin/python tools/check-openapi-coverage.py \
  --env staging \
  --source live \
  --compare-snapshot \
  --fail-missing \
  --fail-placeholder
```

### Completion criteria

- Repository quality CI passes without a live target or secrets.
- Staging API CI uses no Auth0 Management API credential and creates no
  identity-provider users.
- Auth smoke, endpoint behavior, authorization, and live drift results are
  separately diagnosable.
- Production workflow can select only explicitly production-safe read-only
  tests.
- All non-deferred operations retain non-placeholder coverage and meaningful
  behavioral assertions.
- Deferred admin/global operations remain explicit.
- Documentation and workflows describe the same provider-neutral session
  model.
