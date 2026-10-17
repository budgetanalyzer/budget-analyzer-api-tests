# Admin API Coverage With Lazy Local Provisioning

Implement the user's selected option 2: lazily provision a reusable local admin,
stop for manual database role assignment, cover public admin reads and admin
updates to test-created user statement formats, and test deactivation using one
fresh disposable normal account per scenario execution. Creating this plan does
not execute tests or provision accounts.

All phases execute in this repository. Sibling contracts and architecture may
be read, but services, deployment configuration, and databases must not be
modified. Use the public HTTPS browser/gateway path, existing runtime trust,
and shared pacing. No new dependencies or prerequisite installers are expected.
Preserve normal-user coverage, offline collection, secret redaction, and the
existing production read-only smoke selection. Do not run Git write commands.

| Surface | Approved result |
| --- | --- |
| `searchTransactions`, `countTransactionsAcrossUsers`, `getUsers`, `getUser` | Admin success and normal-user `403` coverage. |
| `deactivateUser` | Success, repeat/idempotency, missing target, normal-user denial, audit state, and public session revocation using a fresh test-created target. |
| `listFormats`, `getFormat`, `updateFormat` | Admin cross-user reads and updates confined to USER formats created by the same test. |
| Currency `create`, currency `update`, `importLatestExchangeRates` | Remain deferred. Add no authenticated write requests, including negative tests. Existing unauthenticated coverage remains unchanged. |
| SYSTEM statement-format creation/update | Remain deferred at scenario level; do not modify seeded formats or add global catalog entries. |
| Internal Session Gateway revocation | Remains untestable by this public harness. |
| Dependency failures and inactive-account heartbeat fallback | Remain deferred where execution requires fault/state controls outside this harness. |

Five of the original eight deferred operations will gain successful admin
execution; three global currency/rate operations remain deferred. Operation
markers are not proof that every response or admin scenario is covered.

Local retention is intentional: three reusable identities (primary, secondary,
admin), plus one disposable target per deactivation scenario execution. Leave
the target deactivated after success. Interrupted executions may leave active
targets; preserve their provenance and credentials without sweeping accounts
from earlier runs. There is no public reactivation/delete API or generic janitor.

Live validation requires the documented pinned Python environment, local
deployment, network, runtime HTTPS trust, browser system dependencies, hosted
signup, and the user's manual ADMIN assignment. Read AGENTS, README, and the
phase-specific context first. Missing prerequisites must be reported, not
installed or bypassed. A promotion stop is expected and is not a successful
live gate; resume that phase after the user promotes the account. Prerequisite
checks remain at the operation requiring them, not speculative network probes.

## Phase 1: Reusable Admin Identity And Promotion Stop

### Workspace

.

### Goal

Provide a selectable admin smoke with lazy signup, identity separation,
permission validation, and one actionable manual-promotion stop.

### Scope

Auth/configuration, persistence, pytest fixtures/policy, admin smoke, deterministic
tests, and operator documentation.

### Non-goals

Database access, automatic promotion, disposable targets, admin business-route
coverage, production admin access, rate-policy changes, and dependency changes.

### Required context

- `src/api_tests/auth.py`, `local_credentials.py`, `browser_login.py`,
  `session.py`, `config.py`, `pytest_plugin.py`, `logging.py`, `local_runner.py`.
- `tests/unit/auth/`, `tests/unit/config/`, `tests/unit/pytest_plugin/`,
  `tests/api/session_gateway/test_user.py`, `.env.example`, `environments/`.
- `schemas/session-gateway-openapi.yaml`, `src/api_tests/session_gateway_assertions.py`.
- `../permission-service/docs/authorization-model.md` and its role seed migrations.
- `../orchestration/docs/architecture/security-architecture.md` and
  `../orchestration/docs/agents-md-checkstyle.md` before editing AGENTS.

### Execution steps

1. Extend role handling explicitly to primary, secondary, and admin; eliminate
   binary-role fallthroughs. Add declarative names `BA_ADMIN_TEST_USERNAME`,
   `BA_ADMIN_TEST_PASSWORD`, and `BA_ADMIN_SESSION`. Exact-local generated
   credentials and signup state belong only in owner-only ignored `.env.local`.
   Preserve complete pairs, override rules, and interrupted-signup recovery.
   Staging uses pre-provisioned credentials or supplied sessions.
2. Add lazy `admin_session`/`admin_gateway_client` fixtures and declared `admin`
   and `admin_auth_smoke` markers. Exclude admin scenarios from production
   before fixtures, even on explicit selection. Preserve offline collection
   and ordinary selector semantics. Unrelated selections must not read/create
   admin credentials or log the admin in.
3. Validate the public `/auth/v1/user` response, ADMIN membership, and required
   permissions: `transactions:read:any`, `users:read`, `users:write`,
   `statementformats:read:any`, `statementformats:write:any`. A valid normal
   account missing ADMIN causes `pytest.exit(..., returncode=2)` with:

   > Please promote the test admin user to the ADMIN role in the database.
   > Add ADMIN while retaining USER. The local account username is stored as
   > BA_ADMIN_TEST_USERNAME in .env.local. Then rerun the same command.

   For external credential overrides, identify the configured source instead.
   Supply a stable public identity identifier when needed for disambiguation;
   email alone is not a unique database identity. Never dump credentials.
4. Treat ADMIN-with-missing-permissions as a separate authorization configuration
   failure. Do not relabel HTTP errors, malformed responses, TLS/browser failures,
   or expired supplied sessions as missing promotion. Keep signup completion
   separate from authorization readiness.
5. Reuse the account and perform fresh browser login on rerun to reload grants.
   Supplied mode requires a refreshed externally supplied session when grants
   change; never silently fall back to signup. Promotion is a permission-service
   role assignment, not an identity-provider role or forged header.
6. Enforce distinct public subjects, not merely different cookies. Reject reused
   credential slots and ensure ordinary principals remain normal users before
   relying on their denial results. Compare only requested identities; an
   admin-only smoke must not force secondary login.
7. Make admin scenario business setup depend on successful admin validation.
   Missing promotion must stop before its business fixtures or disposable target
   are created. Do not add an eager global authentication preflight.
8. Update README, auth/environment documentation, `.env.example`, and AGENTS in
   the same change. Document exit code 2, promotion/reuse, staging, and selectors
   `./run-tests -m admin_auth_smoke` and `./run-tests -m admin`.

### Implementation notes

Retain USER when adding ADMIN; effective permissions are a supported union.
Never substitute the admin for primary or secondary. Reuse existing browser
signup, credential persistence, and process-wide pacing.

### Validation

- Run `.venv/bin/python -m pytest tests/unit/auth tests/unit/config tests/unit/pytest_plugin --env local`.
- Cover role dispatch, partial pairs, recovery, duplicate subjects, permission
  failures versus promotion stops, exit propagation, redaction, lazy selectors,
  supplied mode, and production policy before fixtures.
- Run both documented offline API collection gates, Ruff, and strict mypy.
- Run `./run-tests -m admin_auth_smoke` locally: first use provisions one account
  and stops. After manual assignment, rerun twice and verify account reuse.
  Stop/report missing prerequisites; do not declare live acceptance complete
  before the promoted smoke succeeds.

### Completion criteria

The admin smoke succeeds after promotion, missing promotion exits clearly,
unrelated selections stay lazy, and production/offline paths cannot acquire
admin credentials. Documentation and focused validation are current.

## Phase 2: Explicit Forbidden-Response Coverage

### Workspace

.

### Goal

Test normal-user denial of administrative reads and account accurately for
executed versus deferred 403 responses.

### Scope

Four read denials, typed coverage accounting, marker validation, artifacts,
documentation, and deterministic tests.

### Non-goals

Admin read success, authenticated currency/rate writes, new restricted roles,
and reinterpreting ownership `404` or domain `422` as forbidden responses.

### Required context

- `src/api_tests/openapi.py`, `coverage.py`, `pytest_plugin.py`, `tools/openapi_coverage.py`.
- `schemas/deferred-forbidden-responses.yaml`, `schemas/deferred-admin-operations.yaml`,
  `schemas/openapi.json`, `docs/openapi-coverage.md`.
- `tests/api/auth/test_edge_auth.py`, `tests/api/authorization/`,
  `tests/unit/openapi/`, `tests/unit/tools/test_openapi_coverage_tools.py`.

### Execution steps

1. Add schema-backed normal-user 403 cases for `searchTransactions`,
   `countTransactionsAcrossUsers`, `getUsers`, and `getUser`. Use a validated
   normal principal and known test identity for detail. No business mutation or
   admin login is needed merely to execute a denial test.
2. Declare a narrow `forbidden_response(operationId)` marker for unified-contract
   response evidence. Resolve it to exact snapshot method/path and reject
   malformed, unknown, or placeholder-only evidence.
3. Replace the all-deferrals-only check with an exact partition: every documented
   403 has executable marked coverage or an exact deferral, never both/neither.
   Preserve stale/duplicate/method/path validation and schema-backed documented
   401/403 requirements. Update negative unit tests with the model.
4. Decouple response classification from operation-success deferrals. Remove
   only these four 403 deferrals; retain their operation deferrals until success
   tests land. Give currency/rate deferrals an accurate reason such as
   `global_mutation_not_authorized`, rather than claiming the admin is missing.
5. Report response counts/ids separately from operation markers. Denial evidence
   must not make a success-path gap appear covered. Keep all remaining 403 cases
   deferred until their own safe principal/request is implemented.
6. Update marker declarations, README, AGENTS, and coverage documentation with
   the approved normal-principal model and artifact/CLI accounting.

### Implementation notes

The current validator requires a deferral for every operation and derives its
reason from the admin operation manifest. Removing entries alone is insufficient.
Do not hardcode an exemption list that bypasses executable-marker evidence.

### Validation

- Run `.venv/bin/python -m pytest tests/unit/openapi tests/unit/tools tests/unit/pytest_plugin --env local`.
- Run coverage and both offline collection gates; verify gaps, overlap, stale
  entries, and missing executable evidence fail deterministically.
- Run the new read-denial module through `./run-tests`; inspect redacted output.
  Record it as partial validation.

### Completion criteria

Four real 403 cases are counted independently, every remaining documented 403
is deferred explicitly, and operation-success coverage is not overstated.

## Phase 3: Administrative Transaction Search And Count

### Workspace

.

### Goal

Verify cross-user search/count thoroughly with deterministic test-created data.

### Scope

Admin query tests, minimal typed builders, response assertions, operation
deferral updates, and documentation.

### Non-goals

Cross-user transaction writes, unrelated existing-data edits, global fixed
totals, and currency/rate setup.

### Required context

- `schemas/openapi.json`, `src/api_tests/resources.py`, `builders/transactions.py`,
  `run_state.py`, `schemas.py`, `tests/api/transactions/test_transactions.py`.
- Existing admin fixtures and `tests/api/authorization/test_cross_user_resources.py`.
- `../transaction-service/src/main/java/org/budgetanalyzer/transaction/api/request/TransactionFilter.java`,
  public search/count declarations in `TransactionController.java`, and
  `../transaction-service/docs/domain-model.md`.

### Execution steps

1. After admin validation, create a compact dataset as primary and secondary
   through public APIs. Use a shared unique run/test discriminator, varied
   amounts/currencies/dates/types/banks/accounts, and sort ties. Resolve owners
   from public responses, never infer internal ids from email.
2. Exercise every documented filter: id, ownerId, accountId, bankName, dateFrom,
   dateTo, currencyIsoCode, minAmount, maxAmount, type, description, createdAfter,
   createdBefore, updatedAfter, updatedBefore. Cover combinations, inclusive
   boundaries, no matches, and documented text semantics. Use server timestamps
   and test discriminators without sleeps or an empty-account assumption.
3. Assert exact controlled result ids, both owners' visibility, owner filtering,
   and consistency with their own-resource reads. Verify stored numeric amount
   semantics across currencies and currency-plus-amount queries without
   conversion. Use existing currency codes; do not create catalog entries.
4. Cover supported sorts/directions, compound unique tie-breakers, default order,
   pagination boundaries/metadata, empty pages, and traversal without duplicate
   or missing controlled rows. Verify unsupported sort and malformed inputs
   against the contract; do not invent unspecified normalization behavior.
5. Compare count, search total, and exact expected sets for relevant filters;
   count must not depend on search pagination. Avoid comparisons requiring
   unrelated global totals to remain constant between requests.
6. Separate pure-read smoke/error tests from setup-producing scenarios, which
   must be marked mutation. Any deleted-row exclusion scenario must also be
   destructive and delete only a row created by that test.
7. Remove these two operation deferrals alongside real success tests; update the
   coverage matrix and document remaining fault-response gaps.

### Implementation notes

Reuse public builders with minimal extensions. Keep lifecycle setup scoped to
the current test, without order dependencies or generic cleanup. All clients
use the shared GatewayClient pacers.

### Validation

- Run focused deterministic helper/schema tests for changed logic, coverage,
  offline collection, Ruff, and strict mypy.
- Run the search/count module through `./run-tests` twice with retained accounts
  to prove old data does not affect results. Check setup-producing markers.

### Completion criteria

Both operations have substantive success/error coverage over their documented
query surface and repeatable cross-user assertions. Six original operation
deferrals remain at this checkpoint.

## Phase 4: User Reads And Statement-Format Admin Scenarios

### Workspace

.

### Goal

Cover user reads and admin format access while confining updates to USER
formats created by the current test.

### Scope

User list/detail queries, format cross-user reads/updates, deferral updates,
and explicit scenario-level gaps.

### Non-goals

SYSTEM format creation/update, seeded-format edits, deactivation, additional
users solely for pagination, and global snapshot assumptions.

### Required context

- `schemas/openapi.json`, admin fixtures, `src/api_tests/resources.py`,
  `builders/statement_formats.py`, `tests/api/statement_formats/`.
- `../permission-service/docs/authorization-model.md` and public UserController,
  UserFilter, UserDetailResponse contracts under its `src/main/java/` tree.
- `../transaction-service/docs/statement-import.md`, public statement-format
  contracts, `src/api_tests/logging.py`, `docs/openapi-coverage.md`.

### Execution steps

1. Resolve reusable test identities through exact public IdP subject filters and
   canonical internal ids. Assert profile consistency without treating email as
   unique or copying identity data to artifacts.
2. Cover id/email/displayName/idpSub/status and creation/update time filters,
   combinations, no matches, documented text matching, supported sort directions,
   stable tie-breakers, paging/metadata, and invalid sort/filter inputs. Verify
   returned-row properties without fixed global totals or quiescence assumptions.
   Use exact identity filters for deterministic membership; Phase 5 supplies
   positive DEACTIVATED membership.
3. Read each test identity's details: id/subject, profile, ACTIVE status, roles,
   timestamps, and inactive audit fields. Compare list/detail and verify a
   missing user's schema-backed 404.
4. Create USER formats as primary and secondary. Require admin list/get to see
   both while preserving normal-user isolation. Exercise includeHidden and
   caller-specific preferences on test-created formats, distinguishing hidden
   state from ownership visibility.
5. Update only those USER formats as admin and verify owner-visible changes,
   unchanged id/owner/scope/type, supported metadata fields, and invalid updates.
   Do not assume read:any grants foreign hide/unhide access; those routes retain
   an ownership boundary. Mark setup/updates mutation and applicable preference
   removal semantics destructive.
6. Keep existing system-format reads read-only and record SYSTEM creation/update
   as deferred scenarios despite general createFormat/updateFormat markers.
7. Remove `getUsers` and `getUser` operation deferrals with success tests. Update
   README and coverage docs. Harden redaction for arbitrary profile/email query
   values, URL encoding, responses, and assertion/JUnit output; generated-email
   pattern matching alone is insufficient.

### Implementation notes

Pure admin reads are readonly but not production_safe. Do not edit existing
profiles or unrelated catalog entries. Preserve subject identity checks across
all three reusable accounts.

### Validation

- Run focused query/response/redaction unit tests, coverage/collection, Ruff,
  and strict mypy.
- Run new user/format modules twice and existing normal-user ownership tests.
- Inspect artifacts for secrets/profile leakage without printing secrets.

### Completion criteria

All four deferred reads have success/denial coverage, plus admin format reads
and test-created USER-format updates. Four operation deferrals remain:
deactivation and the three currency/rate operations.

## Phase 5: Disposable User Deactivation And Public Revocation

### Workspace

.

### Goal

Prove deactivation, role removal, idempotency, and revocation of multiple active
sessions using one fresh normal account owned by this scenario execution.

### Scope

Disposable provisioning/provenance, one consolidated lifecycle, denial/error
coverage, and precise Session Gateway inventory updates.

### Non-goals

Deactivating existing/reusable users, reactivation, identity-provider deletion,
database cleanup, prior-run account sweeps, fault injection, or internal requests.

### Required context

- Local signup/persistence helpers, admin fixtures, logging, run state, marker
  policy, and user read helpers from earlier phases.
- Both OpenAPI snapshots, Session Gateway inbound inventory and checker.
- `tests/api/session_gateway/test_session.py`, `test_oauth_lifecycle.py`,
  `src/api_tests/session_gateway_assertions.py`.
- `../permission-service/docs/authorization-model.md`, public deactivation
  contract, and `../orchestration/docs/architecture/security-architecture.md`.

### Execution steps

1. Declare `admin_deactivation`. Require exact-local browser mode and mutation
   plus destructive permissions. Skip unsupported staging/supplied-session/
   production cases explicitly at collection before network fixtures. Opaque
   target cookies do not establish ownership and cannot authorize deactivation.
2. After admin validation, create one fresh normal target through hosted signup.
   Persist credentials and pending/completed provisioning state only in owner-only
   `.env.local`, using a unique run/scenario record. Preserve earlier records;
   never reuse prior-run targets. Keep typed identity/provenance and secret-safe
   repr/diagnostics. An interrupted signup retries the same current record,
   rather than silently provisioning more accounts.
3. Bind signup's public subject to its exact admin user lookup and canonical id.
   Require ACTIVE normal status and inequality with all reusable identities.
   Check current-execution provenance before deactivation. Email, a generated
   name prefix, or possession of a cookie alone is insufficient ownership proof.
4. Establish two distinct opaque sessions for the target through public signup/
   login. Verify both are active and map to the same target; retain their original
   cookie values in memory. Use public machine endpoints and a normal API read.
5. Attempt deactivation as an ordinary user against this disposable target;
   require documented 403 and unchanged target status/roles/active sessions.
   If denial fails, stop the lifecycle immediately and report the regression.
6. Deactivate the exact target as admin. Require schema-backed 200, correct id,
   DEACTIVATED status, rolesRemoved equal to its known assigned-role count, and
   sessionsRevoked true. Verify list/detail, empty roles, and deactivation
   actor/time corresponding to the admin and operation.
7. Replay each original cookie via fresh clients against `/auth/v1/user`,
   `/auth/v1/session`, and a protected API read. Require schema-backed 401 and
   documented stale-cookie clearing. Restore the original cookie for every
   request so prior clearing cannot turn later checks into missing-cookie tests.
   Check user/API rejection before heartbeat, preventing heartbeat fallback from
   masking failed bulk revocation. Do not log out target sessions before proof.
8. Repeat deactivation: require success, zero additional roles removed,
   sessionsRevoked true, and unchanged original audit actor/time. Exercise
   ACTIVE/DEACTIVATED list filters on the known target. Require documented 404
   for a freshly generated, confirmed-missing id without touching another user.
9. Verify reusable accounts remain active. Keep one lifecycle rather than
   parameterizing many signups. No generic teardown deactivation or old-account
   sweeps. A 503 can mean deactivation committed before revocation failed:
   report partial state and preserve evidence, without hiding it using retries.
10. Remove `deactivateUser` operation/403 deferrals alongside its tests. Add the
    public case `public-admin-deactivation-revokes-sessions` to the existing
    heartbeat route inventory and mark the lifecycle with its exact case id.
    This proves the public workflow, not the internal hop.
11. Retain `heartbeat-account-inactive` as deferred for the inactive-account
    fallback with a still-present session. Successful bulk revocation cannot
    isolate that branch; clarify its prerequisite accordingly. Keep internal
    revocation untestable. With one added public case, update summaries to
    35 scenarios: 15 covered, 11 deferred, 9 untestable, across 14 routes and
    3 filtered public OpenAPI operations. Full behavior coverage remains false.
12. Document retained accounts, interrupted-run behavior, unsupported modes,
    `./run-tests -m admin_deactivation`, and remaining fault/deleted-account/
    fallback gaps. Update AGENTS' obsolete two-account limit and blanket admin
    deferrals with these approved boundaries, following its authoring standard.

### Implementation notes

No operator step beyond reusable-admin promotion is required. The target never
gets ADMIN. All browser/API requests share existing pacers. Retention is the
accepted end state, not a promise of later automatic cleanup. Missing signup
or trust prerequisites must fail clearly without rotating accounts repeatedly.

### Validation

- Deterministically verify policy/provenance/identity gates prevent signup or
  deactivation when unmet: wrong run, reusable target, duplicate subjects,
  missing promotion, unsupported modes, and interrupted recovery.
- Mock stale-cookie replay, 503 partial outcomes, no hidden retries, and secret
  redaction. Do not inject live dependency faults.
- Run auth/plugin/inventory/security unit tests, coverage, both collection
  gates, Ruff, and strict mypy.
- Run `./run-tests -m admin_deactivation` twice: same reusable admin, distinct
  fresh target each time, no deactivation of earlier targets.

### Completion criteria

The lifecycle proves success, denial, idempotency, audit state, and revocation
of both target sessions. Exactly three original admin operations remain
deferred. Evidence does not claim internal-hop or inactive-heartbeat coverage.

## Phase 6: Integrated Acceptance And Coverage Handoff

### Workspace

.

### Goal

Verify the authorized scope, workflow, accounting, and retained-data behavior.

### Scope

Integration fixes, final documentation, repository gates, and evidence-backed
handoff separating covered and deferred behaviors.

### Non-goals

New features, dependency upgrades, sibling/deployment changes, global resets,
unreviewed snapshot refresh, and Git writes.

### Required context

- All admin tests/support introduced above; AGENTS, README, coverage docs.
- Both deferral manifests, Session Gateway inventory, generated coverage reports.
- `docs/dependency-contract.md` to interpret the existing dependency gate;
  do not change declarations or pins as incidental integration work.

### Execution steps

1. Confirm only `create`, `update`, and `importLatestExchangeRates` remain in
   admin operation deferrals, and no authenticated currency/rate writes or
   SYSTEM mutations were added. Five new 403 operations are covered; remaining
   documented 403s remain explicitly deferred.
2. Reconcile operation/response/scenario accounting, including global-format
   and fault gaps. Confirm the 35-case 15/11/9 Session Gateway baseline agrees
   across inventory, artifacts, CLI, README, and AGENTS.
3. Check selectors, offline behavior, lazy unrelated selections, promotion/reuse
   evidence, staging pre-provisioning, supplied sessions, and unchanged production
   smoke. Report modes not live-validated instead of inferring deployed success.
4. Run the gates below. Missing prerequisites or unexplained product/contract
   failures stop live acceptance. Report them without service fixes, weakened
   assertions, snapshot refresh, or infrastructure privilege changes. Re-run
   only affected checks after fixes.
5. Review isolated artifacts for credential/profile leakage. Hand off commands,
   outcomes, retained-target counts, final classifications, and outstanding
   prerequisites without secrets or claims of full admin behavior coverage.

### Implementation notes

The no-argument local suite includes admin coverage and the single disposable
target lifecycle. Selectors are partial validation. Promotion-stop evidence
comes from Phase 1; do not remove roles or discard credentials to recreate it.

### Validation

```bash
.venv/bin/python tools/validate-environment.py --env local
PYTHONPATH=src .venv/bin/python tools/check-dependency-contract.py \
  --uv .dependency-tools/uv-0.13.0/bin/uv
.venv/bin/python -m pytest tests/unit --env local
.venv/bin/python -m pytest tests/api --env local --collect-only
.venv/bin/python -m pytest tests/api --env production \
  -m "readonly and production_safe" --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
actionlint .github/workflows/*.yml
shellcheck run-tests
./run-tests
```

Earlier focused repeat runs establish reuse and isolation. Do not repeat the
full suite solely for additional evidence once it passes.

### Completion criteria

Required gates pass, full local execution succeeds after manual promotion,
five original admin deferrals have substantive executable coverage, and three
excluded global operations plus remaining scenario gaps are reported accurately.
Any unavailable required live gate remains incomplete, never counted as success.
