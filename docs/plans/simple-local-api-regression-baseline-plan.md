# Simple Local API Regression Baseline Plan

Build a dependable public-API regression baseline before upgrading the services
to Spring Boot 4. Simplify the existing Python harness and update its tests to
the current API. A developer or agent with a working local deployment must be
able to clone this repository and run `./run-tests` without manually creating
test accounts, entering credentials, or running separate bootstrap commands.

Status: Implemented and accepted locally on 2026-10-11. Both final full runs
succeeded with retained credentials and data; the evidence and current limits
are recorded in `docs/local-regression-baseline.md`.

This plan supersedes the provider-neutral session and API coverage completion
plan and the verified-local-TLS prerequisite plan in this directory. Their
historical implementation and results remain useful context. Do not resume
their outstanding phases or recreate their old blockers. The user confirmed
that the agent VM already has trusted local HTTPS and working browser access,
including successful signup through the normal Auth0 flow.

## Decisions And Boundaries

- The completion gate is a manual local run from the agent container. CI/CD,
  staging deployment integration, promotion workflows, and Renovate work are
  out of scope. Leave the `dunno` and `renovate` branches alone.
- Retain Python 3.12, pytest, httpx, Playwright, the `src/` layout, strict mypy,
  and Ruff. Keep a small configuration model, one session value per user,
  `GatewayClient`, shared OpenAPI helpers, and explicit API tests. Introduce no
  general provisioning framework, generated API SDK, or replacement test stack.
- Exercise only the public gateway at `https://app.budgetanalyzer.localhost`
  for this plan's live acceptance. Compose API requests through
  `GatewayClient` as `{origin}{api_base_path}{openapi path}`. Use public browser
  authentication; never use Auth0 Management API provisioning, direct token
  grants, session-store writes, service DNS, or internal data setup.
- Treat the public NGINX policy as a fixed part of that contract. All
  rate-limited API routes share one per-client-IP bucket at 10 requests/second
  with `burst=50` and `nodelay`. Pace every `GatewayClient` instance through one
  process-wide monotonic scheduler at a conservative maximum of 8
  requests/second. Same-origin public-gateway requests made during browser
  authentication reserve slots from that scheduler as well; external identity-
  provider traffic does not. Keep pacing centralized and deterministically
  unit-test it with injected timing. HTTP 429 responses remain failures and are
  never retried. Do not add arbitrary test sleeps, gateway bypasses, forged
  identity headers, or environment-specific rate-limit exceptions.
- HTTPS trust and browser system prerequisites are provided by the runtime.
  Remove harness trust installation and its automatic prerequisite probes.
  Preserve certificate verification. Do not generate certificates, install
  trust anchors, modify the runtime, or add insecure fallbacks.
- Lazily provision at most one primary and one secondary normal user for a
  checkout, then reuse them. Generate unique addresses matching
  `ba-api-test-<random>@test.com` and strong random passwords. Persist actual
  credentials in gitignored `.env.local`; commit only empty credential slots
  and explanatory comments in `.env.example`. The accepted local signup flow
  needs no email verification or MFA automation.
- Reuse a user's session within a pytest run; log in again on a later run.
  Provision and log in the secondary user only when a selected test needs it.
  Use normal pytest fixture dependencies rather than inspecting fixture names
  to infer prerequisite scopes.
- Keep each run's application data uniquely named and assert against known
  IDs or filtered records. Accept leftover data. Delete/hide operations under
  test affect only resources created by that test. Add no generic cleanup,
  rollback, account deletion, or environment-reset system.
- Retain existing production read-only restrictions and supplied-session
  support. Local credentials and automatic signup must never be used outside
  the exact local target. No staging or production execution is required.
- Keep the seven admin/global operations explicitly deferred. No admin
  identities, global-state mutation, or production permission expansion.
- Work only in this repository. The orchestration OpenAPI aggregator is
  read-only contract input during implementation, not a runtime dependency of
  a cloned test repository. Do not modify services to make assertions pass.
- Update README and agent instructions with behavior changes in each phase.
  Do not edit this executable plan while its handler invocation is running;
  record implementation evidence in ordinary repository documentation.

## Phase 1: Simplify The Harness And Separate Unit Tests

### Workspace

.

### Goal

Make configuration, sessions, fixtures, and execution boundaries easy to follow
while retaining current endpoint behavior and safety rules.

### Scope

Simplify existing harness modules and move tests into `tests/unit/` and
`tests/api/`. Retain the old snapshot until the contract migration phase.

### Non-goals

No account signup, endpoint-contract migration, runtime trust work, CI files,
or dependency-automation changes in this phase.

### Required context

- `AGENTS.md`, `README.md`, `pyproject.toml`, and `environments/*.yaml`
- `src/api_tests/config.py`, `session.py`, `auth.py`, `identities.py`, and
  `browser_login.py`
- `src/api_tests/client.py`, `pytest_plugin.py`, `prerequisites.py`, and
  `run_state.py`
- Existing harness, auth-helper, builder, marker-policy, and tool tests
- `../orchestration/docs/agents-md-checkstyle.md` before changing `AGENTS.md`

### Execution steps

1. Create the repository-local Python 3.12 development environment if absent.
   Preserve unrelated work and inspect test dependencies before moving files.
2. Group deterministic harness tests under `tests/unit/` and public HTTP tests
   under `tests/api/`, retaining domain subdirectories where useful. Keep
   unit-test-only pytester configuration local to the unit suite.
3. Replace the session context/identity/bundle chain with one secret-redacted
   session value accepted by `GatewayClient`. Provide independent, session-
   scoped primary and secondary fixtures. Remove the unused acquisition
   protocol, compatibility auth wrapper, duplicate credential wrappers, and
   authentication's unused run-state argument. Keep browser and supplied-
   session acquisition as simple explicit paths.
4. Keep one validated, non-secret environment model. Remove the obsolete
   `per_run_user_boundary`, single-option cleanup setting, and ineffective
   namespace configuration. Retain the existing fixed run-ID convention and
   let pytest own its session lifetime. Simplify redundant config output
   models/serialization without exposing credentials.
5. Remove trust-helper subprocess invocation, inferred preflight scopes, and
   automatic gateway/OpenAPI behavior checks before collection completes.
   Retire the standalone prerequisite CLI and tests for removed machinery.
   Keep concise connection/TLS/authentication errors where requests or login
   actually happen. Gateway contract failures must be ordinary test failures.
6. Preserve early mutation/destructive policy enforcement before client or
   identity fixtures run. Preserve production's supplied primary session,
   read-only selection, and prohibition on browser acquisition. Keep public
   tests independent of user sessions and collection independent of the target.
7. Update README and AGENTS to make this plan authoritative, describe the new
   test layout, and retire obsolete TLS and per-run-user instructions. Add
   short successor notices to the two superseded plans, preserving their
   historical bodies and leaving handler-owned state untouched.

### Implementation notes

Prefer deleting redundant paths over adding compatibility layers. Keep precise
types at external boundaries and useful small helpers; fewer files alone is
not the objective. Browser acquisition should require only the requested user's
credentials. Do not embed account provisioning in configuration loading.

### Validation

```bash
.venv/bin/python -m pytest tests/unit --env local
.venv/bin/python -m pytest tests/api --env local --collect-only
.venv/bin/python -m pytest tests/api --env production -m "readonly and production_safe" --collect-only
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
```

Use mocked transports and browser calls to verify lazy secondary acquisition,
secret-safe representations, and guards before any network/setup action.

### Completion criteria

- Unit tests run without a deployment, browser launch, credentials, or trust
  helper execution; collection remains independent of all live prerequisites.
- Primary and secondary clients each consume one session directly.
- Configuration and run state have no obsolete provisioning/cleanup options.
- Required policy guards remain tested, and documentation describes the
  implemented checkpoint rather than promising later functionality.

## Phase 2: One Local Command And Reusable Browser Accounts

### Workspace

.

### Goal

Make local execution self-starting through `./run-tests`, with lazily created
and persisted browser identities and no manual credential setup.

### Scope

A thin local launcher, a small local credential/provisioning helper, the actual
hosted signup/login flow, and focused account-reuse acceptance.

### Non-goals

No management API, provider-independent signup framework, email inbox/MFA
automation, credential rotation service, parallel provisioning, or CI support.

### Required context

- Phase 1 fixture/configuration boundaries and README
- `src/api_tests/browser_login.py`, `auth.py`, `client.py`, and `logging.py`
- `.gitignore`, `pyproject.toml`, and `environments/local.yaml`
- Existing browser/session helper tests and live authentication smoke

### Execution steps

1. Add executable `./run-tests` as the single documented local entrypoint. It
   resolves the checkout root, creates `.venv` and installs the development
   dependencies when needed, and makes the required Playwright browser
   available. Reuse a working environment on subsequent runs. Do not reinstall
   dependencies, download browsers, or upgrade packages on every invocation.
   Keep shell/bootstrap code thin and application logic in typed Python.
2. Default the command to the full local suite. Pass ordinary pytest selectors
   through for focused development, while preventing arguments from changing
   the target away from the exact local environment. Fail clearly if Python
   3.12 or a required runtime capability is unavailable; do not administer the
   deployment or repair host/VM trust. Preserve meaningful nonzero exit status.
3. Add `.env.example` with empty `BA_PRIMARY_TEST_USERNAME`,
   `BA_PRIMARY_TEST_PASSWORD`, `BA_SECONDARY_TEST_USERNAME`, and
   `BA_SECONDARY_TEST_PASSWORD` slots. No copy/edit step is required. Load
   `.env.local` automatically as data, never by shell evaluation. Environment
   variables may override complete credential pairs; do not mix one identity's
   username with another source's password or silently replace partial pairs.
4. When a local browser fixture first needs an identity with no saved or
   explicitly supplied credentials, generate its unique approved email slug
   and a cryptographically random password satisfying the hosted password
   policy. Save the pair before signup with owner-only file permissions.
   Preserve existing values and unrelated file content. Create the account
   through signup reached from the public application login flow.
5. Reuse the same stored identity on later runs. Keep interrupted provisioning
   recoverable with that identity, using only minimal local pending/completed
   state if needed. Failed login, timeout, an existing-account response, or a
   partial file must never trigger replacement-user generation. Diagnose
   unresolved failures clearly. Do not log passwords or persist cookies.
6. Call this small helper lazily from the primary/secondary session fixtures;
   reuse the acquired session for that user's requests. Require no additional
   operator provisioning command. Public tests, unit tests, and collect-only
   runs must not generate credentials or create users. Verify a usable session
   through the public Session Gateway, and keep browser selectors in the
   browser module.
7. Restrict reading local credential files, credential generation, and signup
   to the exact declared local HTTPS target before accessing secrets. Keep
   supplied-session execution available without browser startup. Avoid browser
   traces, screenshots, storage dumps, assertion bodies, or subprocess output
   exposing secrets. Update README and AGENTS with the supported workflow.

### Implementation notes

The runner already has trusted HTTPS and browser access, as confirmed by the
user. Exercise that path during implementation; do not create a prerequisite
project to reconfirm it. A fresh clone still needs Python dependencies, which
the launcher handles. Document external host/browser prerequisites honestly.

Two accounts are a maximum for the ordinary full suite, not a per-test or
per-run allocation. A fresh independent checkout with no retained credentials
can create its own accounts; do not build a shared account registry. Losing
`.env.local` loses the automatic reuse path, so preserve that file between runs.

### Validation

- Run focused unit tests for missing, existing, and partial credential files;
  interrupted signup; failed login; wrong target; and secondary laziness. Mock
  signup in unit tests. Check file permissions and secret redaction.
- Run `./run-tests -m auth_smoke` against local from the agent container. It
  must generate and save the primary credentials and complete normal signup
  without operator input. Run it again and prove reuse, without displaying
  secret values. Use existing saved credentials if already present.
- Confirm a primary-only run leaves secondary provisioning unused. Test the
  secondary helper locally once when needed and retain its credentials.
- Check `./run-tests --collect-only` creates no credentials or accounts and
  `git check-ignore .env.local` succeeds. Validate `.env.example` has no working
  credentials. Run Ruff, strict mypy, and the affected unit tests.

### Completion criteria

- One local command bootstraps Python dependencies and reaches authenticated
  public API execution with generated, persistent credentials.
- Repeated executions reuse accounts and acquire fresh sessions as needed.
- Missing secondary credentials cannot block primary-only tests.
- Signup is local-only; secrets remain outside tracked files and artifacts.
- The focused live authentication path passes. The old endpoint contract is
  explicitly awaiting Phase 3, not claimed as a passing full baseline.

## Phase 3: Adopt The Current Public Contract

### Workspace

.

### Goal

Bring the snapshot, payload helpers, and existing endpoint scenarios into
agreement with the current transaction and static saved-view contracts.

### Scope

An intentional contract refresh and the corresponding successful API scenarios,
with simpler resource setup and accurate operation inventory.

### Non-goals

No service-source coupling, generated SDK, automatic snapshot acceptance,
restoration of removed endpoints, or admin/global tests.

### Required context

- `schemas/openapi.json` and `schemas/deferred-admin-operations.yaml`
- `../orchestration/docs-aggregator/openapi.yaml` as read-only reference
- `src/api_tests/openapi.py`, `coverage.py`, `schemas.py`, and coverage tools
- Transaction/view builders, `resources.py`, and `tests/api/`
- The deployed public `/api-docs/openapi.json`

### Execution steps

1. Compare the checked-in snapshot, current aggregator, and deployed local
   document. Review operation methods/paths, input shapes, response schemas,
   and documented behavior. Intentionally refresh the snapshot after resolving
   discrepancies. Never rewrite the contract as a side effect of testing.
2. At plan creation the old inventory has 43 operations; the aggregator has
   40, including 33 non-deferred operations. Confirm the current inventory
   rather than hardcoding those counts. Add `createTransaction`, `cloneView`,
   and `updateViewTransactions` coverage. Retire the six removed pin/unpin and
   exclude/unexclude operations and their obsolete payload helpers.
3. Implement a small direct manual-transaction creation helper. Use it for
   transaction CRUD, saved-view setup, and later ownership tests. Preserve
   namespacing and typed resource IDs. Test manual creation with optional
   `bankName` omitted as well as present; the response no longer requires it.
4. Update import-specific helpers/tests to repeated `files` multipart parts
   and ordered per-file preview tokens, import requests, and results. Keep
   statement-format wizard multipart behavior as its own documented contract;
   do not apply the transaction-preview change to unrelated endpoints.
5. Replace criteria/open-ended saved-view setup with explicit transaction ID
   collections. Change rename/update to `PATCH`. Test creation, read, rename,
   membership delta, clone, and delete using current request/response shapes.
   Membership now returns `transactionIds`, not matched/pinned/excluded groups.
6. Apply the existing OpenAPI response validator before behavioral assertions.
   Assert exact documented success statuses, empty bodies for `204`, and
   meaningful persisted fields. Keep clear typed JSON boundaries without
   repeating every schema type check in each scenario.
7. Consolidate coverage checking and report export behind one command. Retain
   intentional snapshot refresh separately. Fail on unrecognized test marker
   IDs, missing non-deferred operations, placeholder-only coverage, and stale
   deferred method/path entries. Retire duplicate tool entrypoints and update
   their tests and documentation.

### Implementation notes

The existing API was intentionally changed before the Spring upgrade; this
phase adopts those changes explicitly. Do not execute the superseded plan's
criteria/pin/exclude work. Exact field names and validation rules come from
current OpenAPI, including that a per-file transaction list may be empty.

Keep endpoint scenarios handwritten. Operation markers are an inventory, not
proof that a request passed or that behavior is complete. The final run needs
both passing tests and a complete non-deferred inventory.

### Validation

- Run affected builder/schema/coverage unit tests and full API collection.
- Run the migrated transaction, view, and statement-format tests with
  `./run-tests` selectors against local, fixing only this repository's defects.
- Run snapshot coverage with missing/placeholder failures enabled; every
  current non-deferred operation must have a successful-path scenario.
- Verify setup for unrelated CRUD/view tests no longer invokes the import
  wizard. Run Ruff and strict mypy.

### Completion criteria

- Snapshot and tests target the current API, with no removed-operation markers.
- Manual creation, multi-file import, and static-view happy paths pass locally.
- All current non-deferred operations have real mapped tests; deferred global
  scope remains explicit and unchanged unless the actual contract requires a
  reviewed inventory correction.
- API execution needs no sibling repository or service implementation files.

## Phase 4: Strengthen Observable Behavior And Validation

### Workspace

.

### Goal

Make the suite sensitive to functional regressions in persistence, query
binding, serialization, imports, and request validation during Spring upgrades.

### Scope

Focused positive and negative scenarios using controlled public-API data.

### Non-goals

No exhaustive generated input matrix, database assertions, arbitrary test
sleeps, performance benchmarks, or generic cleanup framework. The centralized
public-gateway pacer is contract compliance, not test-specific timing.

### Required context

- Current OpenAPI request/response schemas and operation descriptions
- `tests/api/`, public data helpers, and deterministic CSV/PDF fixtures
- `src/api_tests/assertions.py`, `schemas.py`, and `logging.py`

### Execution steps

1. Complete transaction create/update/delete and bulk-delete postconditions
   with subsequent public reads. Verify exact controlled amounts, dates,
   direction, and editable values. Prove search filters, ordering, pagination,
   and count agree with a uniquely identifiable created record set, even when
   the account has older data.
2. Exercise ordered multi-file preview/import with exact row values and
   aggregate/per-file counts. Add representative duplicate-skipping and
   explicit-duplicate cases consistent with the documented same-file versus
   earlier-file behavior. Prove a documented invalid multi-file request leaves
   no partially imported test records using public queries. Do not inspect
   provenance tables or inject internal failures.
3. Verify static membership additions/removals through subsequent reads, exact
   transaction counts, persisted rename, and clone independence after changing
   one view. Verify delete and documented treatment of deleted member
   transactions. Compare unordered ID collections as sets when documented.
4. Retain format update/hide/unhide postconditions and deterministic CSV/PDF
   wizard success cases. Strengthen controlled preview values where assertions
   currently check only that a list is nonempty. Verify saved formats through
   subsequent public reads.
5. Add representative documented failures for missing required fields,
   malformed JSON, invalid enums/values, missing multipart parts, invalid
   files, invalid import tokens, and invalid view membership input. Derive
   empty-list cases from the current schema; do not presume every empty list
   is invalid. Assert documented statuses and stable error fields only.
6. Keep unauthenticated edge behavior as a real test. Add an authenticated
   unknown-route test that proves the API fails closed without returning the
   SPA. Validate public API docs without acquiring a session. Keep auth-smoke
   results identifiable and runnable with a selector, without string-based
   special cases that silently skip them during the normal full local run.
7. Keep diagnostics secret-safe, including failure output outside the request
   logger. Use concise redacted response context. Update documentation for the
   final marker grammar and focused test commands.

### Implementation notes

Prefer a few clear scenarios per behavior. Reuse small helpers only for repeated
setup or assertions; do not encode an entire second API model. Keep strict
typing, schema validation, and explicit expected values working together.

For global read-only currency/rate data, assert useful documented behavior
without mutating the catalog or assuming uncontrolled exact counts. Required
local scenarios must not silently pass through empty data or unexplained skips.
Use bounded polling only if the public contract actually requires eventual
consistency.

### Validation

- Run the affected API modules with `./run-tests` selectors against local.
- Rerun a representative CRUD/query/import/view selection with the same users
  and retained data to expose empty-account assumptions.
- Run helper unit tests only where behavior changed, snapshot marker coverage,
  Ruff, strict mypy, and production-safe collection without live execution.

### Completion criteria

- CRUD and membership tests prove persistence and exact controlled effects.
- Imports cover multiple files, duplicate behavior, and observable atomicity.
- Representative JSON and multipart binding/validation failures are covered.
- Gateway/API routing assertions appear as test outcomes, not prerequisite
  aborts, and no required happy path accepts a business error as success.

## Phase 5: Prove Isolation Between The Two Reusable Users

### Workspace

.

### Goal

Protect ownership boundaries with direct two-user public API scenarios.

### Scope

Transaction, statement-format, saved-view, relationship, and import-token
ownership tests using the lazy secondary fixture.

### Non-goals

No admin endpoints, role promotion, identity-provider administration, new
per-test users, or internal inspection of owners/permissions.

### Required context

- Current OpenAPI ownership and error contracts
- Primary/secondary fixtures and local account persistence from Phase 2
- Manual transaction, statement-format, import, and static-view helpers
- `schemas/deferred-admin-operations.yaml` and marker policy

### Execution steps

1. Add authorization tests using two distinct persisted normal users. Verify
   secondary acquisition is lazy and cannot accidentally reuse the primary
   session or credentials. Use public identity/session information where
   available; do not inspect provider administration APIs.
2. Create primary-owned transactions, a user-scoped statement format, and a
   saved view. Test secondary direct access and representative mutations with
   the documented non-disclosure or forbidden status. Verify the primary's
   records remain unchanged after rejected mutations.
3. Test that secondary-owned view creation or membership updates cannot attach
   a primary-owned transaction. Test clone ownership boundaries. Assert no
   partial membership changes after rejected mixed-owner requests when the
   contract promises atomic behavior.
4. Prove a primary preview token cannot authorize a secondary import, using the
   documented error contract and observable absence of created records.
5. Mark setup/request behavior accurately with authorization and mutation/
   destructive markers. Fail required local authorization execution clearly
   if a usable distinct secondary identity cannot be established; do not skip
   the boundary tests and report a successful baseline.

### Implementation notes

Prefer straightforward primary-client/secondary-client calls. No generic
permissions matrix is needed. A protected record's `404` and a missing-session
`401` prove different things; keep authenticated ownership assertions precise.

### Validation

```bash
./run-tests -m authorization
./run-tests -m authorization
```

Verify the first invocation lazily supplies the secondary account if needed,
and the second reuses both identities without account creation. Run affected
unit checks, Ruff, strict mypy, and production-safe collection.

### Completion criteria

- Direct resource and relationship ownership boundaries pass locally.
- Rejected cross-user operations have no observable unauthorized side effects.
- Two reusable identities suffice for the suite; production-safe selection
  needs neither secondary credentials nor provisioning.

## Phase 6: Complete The Manual Regression Baseline

### Workspace

.

### Goal

Deliver the single-command local experience and record a passing baseline that
can remain fixed while the services move to Spring Boot 4.

### Scope

Explicit contract drift reporting, final command integration, operator
documentation, and manual acceptance from the agent container.

### Non-goals

No CI/CD, staging execution, deployment changes, recurring jobs, dependency
bots, custom report dashboard, or Spring Boot implementation upgrade.

### Required context

- README, AGENTS, `.env.example`, `./run-tests`, and `pyproject.toml`
- Completed unit/API suite, snapshot inventory, and OpenAPI tooling
- Request-log redaction and pytest report configuration
- `../orchestration/docs/agents-md-checkstyle.md` before changing AGENTS

### Execution steps

1. Add deterministic request pacing to `GatewayClient` for the public NGINX
   contract: all client instances in the process share one monotonic scheduler
   capped at 8 requests/second. Inject clock and sleep functions for unit tests,
   reserve slots from that scheduler for browser authentication's same-origin
   public-gateway traffic, keep pacing out of endpoint tests, and return HTTP
   429 responses without retry. Do not use NGINX bypasses, forged client-IP
   headers, or per-environment exceptions.
2. Make `./run-tests` with no arguments run the complete local unit and API
   suite, check non-deferred operation coverage, and report failures with a
   nonzero exit. Keep focused pytest selection available, but identify focused
   runs as partial validation. No separate preflight, credential-editing, or
   coverage-export command may be required for a normal full run.
3. Compare the live public OpenAPI document with the checked-in baseline.
   Report additions/removals and method/path changes plus changed parameters,
   request bodies, response definitions, and component schemas. A small
   normalized structural comparison with readable changed locations is enough;
   do not build a semantic compatibility engine. Ignore irrelevant document
   metadata such as server URLs and key order. Report contract differences
   explicitly and fail the full gate pending intentional review/refresh.
4. Keep snapshot refresh an explicit developer action and ordinary collection
   offline. Ensure a clone can run using its own checked-in files and public
   target alone; the orchestration aggregator is not required at execution.
5. Write minimal local artifacts: pytest results, coverage/drift details, and
   redacted request diagnostics under `artifacts/`. Keep runs distinguishable
   without mixing old log entries. Do not store browser state, credentials,
   unredacted token fields, or provisioning form captures.
6. Document `./run-tests` as the primary README command, including first-run
   setup, lazy signup, `.env.local` reuse, namespaced residue, and focused
   execution. Explain that deployment reset is external and optional, and that
   the suite performs no general cleanup. Align AGENTS and remove obsolete
   active commands, prerequisite blockers, and staging/CI completion promises.
7. Run the full command manually from this container against the working
   local deployment, then repeat it with the same credentials and retained
   test data. Use the original first-run acceptance from Phase 2 as evidence of
   automatic account creation; never delete credentials to simulate a clean
   checkout and generate unnecessary additional users.
8. Record the accepted baseline in `docs/local-regression-baseline.md`: test
   revision/worktree state, snapshot identity, Python/dependency versions,
   target, run results, deferred scope, and externally supplied service
   revision identifiers where available. State any unavailable service version
   evidence honestly. Keep exact installed dependency versions in an ignored
   run artifact for replay; no dependency-automation project is required.
9. Explain the upgrade workflow: rerun the accepted tests and snapshot against
   upgraded services; investigate failures before changing expectations.
   Intentional public contract changes require a separate explicit review.
   If acceptance exposes a product defect, record the failing public scenario
   and leave completion open rather than weakening assertions or editing a
   service from this repository.

### Implementation notes

Unit tests and lint/type checks are local development checks, not a proposed CI
system. Keep setup and execution simple enough that the README needs one normal
run command. Retain current production safety without developing a production
runner or promising staging validation.

### Validation

```bash
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
.venv/bin/python -m pytest tests/api --env production -m "readonly and production_safe" --collect-only
./run-tests
./run-tests
git status --short
```

Also use unit tests with synthetic document changes to prove the drift check
detects same-operation request/response changes and leaves the snapshot intact.
Use injected fake monotonic time to prove that separate `GatewayClient`
instances share 125 ms request-start spacing and that HTTP 429 is not retried.
Verify credential files and generated reports remain ignored and no secrets
appear in captured failure diagnostics.

### Completion criteria

- `./run-tests` is sufficient for a new checkout with the documented runtime
  prerequisites and an already-working local deployment; the developer does
  not supply test usernames/passwords or invoke a second setup command.
- Two full manual local runs pass using the same users and retained data.
  All required non-admin scenarios execute without unexplained skips or xfails;
  every non-deferred operation has a passing successful-path test.
- Coverage, live contract drift, and test failures all affect the command's
  result. The checked-in contract is never refreshed automatically.
- Baseline evidence and documentation are sufficient to rerun the same suite
  through the Spring upgrade, with explicit limits for deferred global scope.
- No CI/CD configuration, runtime trust implementation, generic cleanup, or
  service-internal dependency was introduced.
