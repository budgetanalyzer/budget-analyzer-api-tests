# Budget Analyzer API Tests

Standalone black-box tests for the Budget Analyzer public gateway API. The
active implementation plan is
`docs/plans/simple-local-api-regression-baseline-plan.md`.

The harness keeps deterministic unit tests separate from deployed public API
tests. Its checked-in OpenAPI snapshot and handwritten scenarios follow the
current public contract.

## Local Run

From a checkout, run:

```bash
./run-tests
```

This is the single supported local entrypoint. It requires a `python3.12`
command, creates `.venv`, installs development dependencies, and downloads
Playwright Chromium only when each is missing or stale. Later runs reuse them.
With no arguments it runs the complete unit and public API suite, checks
non-deferred OpenAPI marker coverage, and compares the live public contract to
the checked-in snapshot. Any failed test, incomplete coverage, or contract
drift makes the command fail. It always selects `environments/local.yaml` and
rejects `--env`.

Ordinary pytest selectors pass through. The command labels these runs as
partial validation because they do not include the coverage and live-contract
gates, for example:

```bash
./run-tests -m auth_smoke
./run-tests tests/unit/auth
./run-tests --collect-only
```

The local deployment must already be running at
`https://app.budgetanalyzer.localhost`. The runtime must provide trusted HTTPS,
network access, and Playwright system libraries. The repository reports missing
runtime prerequisites but does not administer the deployment, install trust
anchors, or weaken certificate verification.

The public gateway contract has one per-client-IP NGINX bucket shared by all
rate-limited API routes: 10 requests/second with `burst=50` and `nodelay`.
Every `GatewayClient` in a test process deliberately shares a monotonic,
fixed-interval pacer capped at 8 requests/second. This conservative client-side
pacing applies centrally to API and raw gateway requests, including requests
made by different authenticated clients. Same-origin public-gateway requests
during browser authentication reserve slots from that same pacer; external
identity-provider traffic does not. Individual tests do not sleep or implement
their own throttling. An HTTP 429 is returned directly to the test and remains
a failure signal rather than being retried.

The first test that needs each normal user generates its credentials and signs
up through the public hosted flow; no credential editing or separate bootstrap
command is needed. Preserve the resulting gitignored `.env.local` so later runs
log in as the same users.

Environment files under `environments/` are validated, declarative, and
non-secret. They contain only public target settings and names of environment
variables that hold credentials or opaque sessions. Validate one with:

```bash
.venv/bin/python tools/validate-environment.py --env local
```

HTTPS verification always uses the runtime's normal trust stores. The harness
does not install certificates, invoke trust helpers, probe the gateway before a
run, or provide an insecure fallback. Connection and certificate problems are
reported by the request or browser login that encounters them.

## Test Layout

- `tests/unit/` contains deterministic harness, configuration, builder,
  marker-policy, schema, and tool tests. It requires no deployment, browser
  launch, credentials, or network access.
- `tests/api/` contains tests that exercise deployed public HTTP routes through
  `GatewayClient`.

Collection is offline and does not create credentials or accounts. Because a
selector is supplied, this is a partial validation run:

```bash
./run-tests --collect-only
```

Run the unit suite with:

```bash
.venv/bin/python -m pytest tests/unit --env local
```

## Authentication

Each requested user produces one redacted session value. The primary and
secondary session-scoped fixtures acquire independently, so tests that only use
the primary client do not request secondary credentials or start a secondary
browser login.

On first use, a local browser fixture generates a normal-user email and strong
password, saves the pair to gitignored `.env.local` with owner-only
permissions, and signs up through the hosted page reached from the public
application login flow. Later runs reuse the identity and perform a fresh
browser login. Preserve `.env.local` between runs; losing it loses automatic
account reuse. A primary-only selection does not generate or log in the
secondary account.

`.env.example` documents the four credential slots but contains no working
values. A complete process-environment pair may override one saved identity:
`BA_PRIMARY_TEST_USERNAME` with `BA_PRIMARY_TEST_PASSWORD`, or the equivalent
secondary pair. Partial pairs fail clearly and are never mixed with file
values. Failed login or interrupted signup preserves the same saved identity
for diagnosis and retry. Passwords and session cookies are not written to test
artifacts.

Authentication smokes are identifiable as `auth_smoke` and
`secondary_auth_smoke`, run as ordinary outcomes in the full local suite, and
remain directly selectable:

```bash
./run-tests -m auth_smoke
./run-tests -m secondary_auth_smoke
```

A focused primary-only selection does not request the secondary identity. The
full suite includes both smokes and therefore provisions or logs in both
reusable identities. In both browser and supplied-session modes, each smoke
verifies its own session with an explicit public `GET /auth/v1/user` request
and requires HTTP 200; invalid or redirected sessions fail the smoke.

Authorization scenarios request both users lazily and reject reused local
usernames or session values before relying on cross-user results. Each
browser-backed session is verified through the public `/auth/v1/user` route
during acquisition. Run the scenarios twice to exercise first-use provisioning
followed by reuse of the same two identities:

```bash
./run-tests -m authorization
./run-tests -m authorization
```

These scenarios assert non-disclosing `404` responses for direct foreign
resources, owner-scoped saved-view membership and clone behavior, and preview
token ownership. Rejected operations are followed by owner-visible reads to
prove that records and memberships did not change and imports created no rows.
A missing or indistinct secondary identity fails local authorization execution;
production-safe collection does not request it.

Automatic credential-file access and signup are restricted to the exact local
target. Staging remains pre-provisioned, and production remains
supplied-session-only and read-only.

Opaque supplied sessions can bypass browser startup:

```bash
BA_PRIMARY_SESSION=... BA_SECONDARY_SESSION=... \
  .venv/bin/python -m pytest tests/api --env local \
  --session-mode supplied_sessions
```

Production remains supplied-session-only and read-only. Run only the explicit
production-safe selection:

```bash
BA_PRIMARY_SESSION=... \
  .venv/bin/python -m pytest tests/api --env production \
  -m "readonly and production_safe"
```

Mutation and destructive policy is applied during collection, before client or
session fixtures can run. Public-route tests use the unauthenticated client and
do not acquire a user session.

## OpenAPI Snapshot And Coverage

The intentionally refreshed public contract is checked in at
`schemas/openapi.json`. One command checks every test marker against that
snapshot, rejects missing and placeholder-only coverage, validates the exact
method and path of deferred admin operations, and writes
`artifacts/openapi-coverage.json`:

```bash
.venv/bin/python tools/check-openapi-coverage.py --env local
```

The full `./run-tests` gate fetches the public OpenAPI document and reports
added or removed operations, method/path moves, and changed parameters, request
bodies, responses, or component schemas. Server URLs, mapping key order, and
other irrelevant metadata do not cause drift. Differences are review failures;
the gate never updates the checked-in baseline. After these preflight checks,
the runner starts pytest in a fresh Python process so pytest plugins initialize
independently from libraries imported while checking the live contract.

Snapshot refresh remains a distinct, intentional developer action after a
public contract change has been reviewed:

```bash
.venv/bin/python tools/refresh-openapi.py --env local
```

An authenticated unknown path under the API prefix must return a non-HTML 404;
returning the browser SPA is a routing regression. The unauthenticated API edge
and public API-document route are asserted independently.

Transaction preview uses repeated ordered `files` multipart parts and batch
import preserves one token, transaction list, and result per source file.
Statement-format wizard routes retain their separate `file` plus JSON
`request` multipart contract. Ordinary transaction and saved-view setup uses
direct manual transaction creation; static saved views contain explicit
transaction IDs and support rename, membership delta, and clone operations.
Eight admin/global operations are explicitly deferred. This includes both
cross-user transaction search and count, which the current contract describes
as administrative and which normal browser users cannot execute.

The normal-user transaction collection is intentionally an unpaged snapshot.
Its regression scenarios verify exact run-owned records and the user-scoped
count filter binding. Administrative server-side search, sorting, and paging
remain deferred; browser table sorting and pagination are presentation-only.

## Data Isolation

A pytest session owns one UTC run id such as
`ba-api-test-20260605T123456Z-1a2b3c4d`. Resource builders include that id in
generated names and identifiers so assertions do not assume an empty account.
Delete, hide, and unhide tests target only resources created by that test.
Manual transaction creation keeps unrelated CRUD and saved-view setup
independent from the statement import wizard.
There is no generic cleanup registry, janitor, or environment reset path;
leftover user-owned local data is accepted.

Resetting or redeploying the local environment is external and optional. The
suite does not require a clean database, and it never resets deployment state
or broadly removes prior test data.

Related create/read/update/delete assertions are grouped into lifecycle
scenarios, and transaction import scenarios share one run-owned statement
format. Together with centralized 8-requests/second gateway pacing, this keeps
the full suite within the public ingress contract without retries,
environment-specific exceptions, or weakened assertions.

## Markers And Focused Regression Runs

An OpenAPI marker has exactly one operation id:
`@pytest.mark.openapi("operationId")`. Behavioral markers are independent:
`readonly`, `mutation`, `destructive`, `authorization`, `production_safe`,
`auth_smoke`, and `secondary_auth_smoke`. Use normal pytest marker expressions;
the harness does not inspect marker-expression strings to change test behavior.

Focused phase-4 scenarios can be run with:

```bash
./run-tests tests/api/transactions/test_transactions.py
./run-tests tests/api/statement_formats/test_statement_formats.py
./run-tests tests/api/views/test_views.py
./run-tests tests/api/transactions/test_transactions.py \
  -k "transaction_crud or ordered_preview_and_import or invalid_later_import_token"
./run-tests tests/api/views/test_views.py \
  -k "membership or clone or deleted_transaction"
```

Run the same focused selector again with the retained `.env.local` identities
to exercise it against accounts containing data from earlier runs.

## Run Artifacts

Every `./run-tests` invocation writes to a distinct ignored directory under
`artifacts/runs/`. Full runs contain exact installed dependency versions,
Python runtime details, OpenAPI coverage and drift reports, JUnit pytest
results, and redacted public request diagnostics. Focused runs contain their
JUnit result and any request diagnostics. Artifacts never contain browser
storage, provisioning form captures, passwords, authorization headers, or
session-cookie values.

## Spring Upgrade Workflow

The accepted baseline and its limits are recorded in
`docs/local-regression-baseline.md`. During the Spring Boot upgrade, run the
same checked-in tests and snapshot against the upgraded local services. Treat a
failure as a regression to investigate before changing an assertion. Refresh
`schemas/openapi.json` only through a separate explicit review when the public
contract change is intentional; do not make the suite accept an unexplained
service change.

## Quality Gates

```bash
.venv/bin/python -m pytest tests/unit --env local
.venv/bin/python -m pytest tests/api --env local --collect-only
.venv/bin/python -m pytest tests/api --env production \
  -m "readonly and production_safe" --collect-only
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
./run-tests
```
