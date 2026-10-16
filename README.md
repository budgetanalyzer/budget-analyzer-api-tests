# Budget Analyzer API Tests

Standalone contract inventory and black-box tests for Budget Analyzer API
surfaces. Every known inbound surface is recorded, while deployed tests remain
limited to authorized browser-facing HTTPS lanes. Inaccessible internal and
operational behavior is reported explicitly rather than ignored or reached by
bypassing deployment boundaries.

## Local Run

From a checkout, run:

```bash
./run-tests
```

This is the single supported local entrypoint. It uses `python3.12` only to
bootstrap the repository's pinned `uv`; set `BA_API_TEST_PYTHON` to another
Python 3.12 interpreter path when needed. The bootstrap interpreter's patch may
differ from `.python-version`.

On first use, the runner installs the exact reviewed `uv` version into the
ignored `.dependency-tools/` directory, separate from the project graph. That
`uv` installs and caches the exact `.python-version` runtime (currently
3.12.15), then uses only that managed interpreter to synchronize `.venv` from
`uv.lock` with all dependency groups in frozen mode. Every invocation checks
manifest/lock freshness and exact environment parity before reuse. Changes to
`pyproject.toml`, `.python-version`, the `uv` tool pin, or `uv.lock` invalidate
readiness; normal runs never rewrite the lock. Playwright Chromium remains a
lazy install derived from the locked Playwright package and is checked only
after Python synchronization.

With no arguments the command runs the complete unit and public API suite,
checks namespaced OpenAPI and Session Gateway inventory coverage, compares the
live unified public contract to its checked-in snapshot, and writes dependency
parity evidence. Any stale lock, environment mismatch, failed test, incomplete
coverage, or contract drift makes the command fail. It always selects
`environments/local.yaml` and rejects `--env`.

The preflight reports the 40 unified operations separately from the 3 filtered
Session Gateway operations. It also prints every Session Gateway scenario id
by classification, including the 11 deferred and 9 untestable cases, before
reporting unified live-contract drift. The Session Gateway snapshot is
validated offline and exercised semantically by public tests; the runner never
fetches its non-public service-local Springdoc route.

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

Istio ingress owns four independent per-client, per-ingress-instance policies:
login initiation at 1 request/second, login completion at 2 requests/second,
session operations at 5 requests/second, and API requests at 10 requests/second.
The canonical path boundaries, bursts, browser-concurrency assumptions, and
local replica semantics are in the orchestration
[security architecture](../orchestration/docs/architecture/security-architecture.md#ingress-per-client-rate-limiting),
with executable matchers in
`../orchestration/kubernetes/istio/ingress-rate-limit.yaml`. NGINX no longer
owns a blanket API limiter.

The harness uses four fixed-interval paces below those sustained allowances:
0.8, 1.6, 4, and 8 starts per second respectively. It does not reproduce
Envoy's burst buckets. Every `GatewayClient` and same-origin browser request in
one process shares the applicable policy pacer, regardless of account, cookie,
or authentication state. Bare `/login`, its query variants, and unrelated
same-origin assets match no rate policy. External identity-provider requests
also consume no application-ingress reservation. Browser traffic is paused at
Chromium's request stage, including the provider redirect back to
`/login/oauth2/code/idp`, before a classified request reaches the network.
Separate test processes and external callers behind the same public IP are not
coordinated by this in-memory pacing.

Individual tests do not sleep or implement their own throttling. HTTP `429` is
returned directly and never retried. A response carrying
`x-local-rate-limit-policy` is either a shared-pacing regression or evidence of
concurrent traffic outside this harness; it is not a transient response to hide
with a retry. There is no first-use cooldown or callback-timeout padding.

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

## Dependency Contract

`pyproject.toml` declares reviewed compatibility ranges, `.python-version`
selects the supported Python patch release, and `uv.lock` records the exact
cross-platform Python 3.12 graph. The rationale for each direct dependency,
the pinned bootstrap and scanner tools, and the intentional lock-refresh
procedure are documented in [docs/dependency-contract.md](docs/dependency-contract.md).

Validate declarations, lock structure, lock freshness, and the exact hashed
export with the pinned `uv` executable installed by `./run-tests`:

```bash
PYTHONPATH=src .venv/bin/python tools/check-dependency-contract.py \
  --uv .dependency-tools/uv-0.13.0/bin/uv
```

GitHub Actions CI runs on pushes to `main`, pull requests targeting `main`, and
manual dispatch. It performs the same exact-runtime frozen synchronization,
dependency contract check, unit tests, offline local and production-safe API
collection, Ruff checks, and strict mypy check without installing a browser,
acquiring credentials, or contacting a Budget Analyzer deployment.

Generate the same bounded dependency evidence used by the scheduled workflow
with the exact Python in `.python-version`:

```bash
PYTHONPATH=src .venv/bin/python tools/generate-dependency-evidence.py
```

This command recreates the isolated `pip-audit` environment and writes the
validated allowlist under `artifacts/dependency-evidence/`. It contacts only
standard Python package and vulnerability services. Details of the workflow,
artifact, and failure policy are in
[docs/dependency-contract.md](docs/dependency-contract.md). Renovate activation
and repository settings remain separate administrator-owned work.

Repository-specific Renovate extraction, lock maintenance, review rules,
failure triage, and the pre-activation administrator checklist live in
[docs/dependency-automation.md](docs/dependency-automation.md). Read that
document before changing a dependency declaration, runtime or tool pin,
`uv.lock`, dependency workflows, or `renovate.json`. The checked-in preparation
does not mean the Mend App or GitHub dependency settings are active.

Intentional dependency updates are the only time the lock should change. Edit
the reviewed compatibility declaration, use the isolated pinned tool and the
lock-refresh commands in [docs/dependency-contract.md](docs/dependency-contract.md),
then review and validate both the manifest and generated lock. Do not delete
the frozen flag or run an updating lock command as part of ordinary setup.

## Local Setup Troubleshooting

- If `python3.12` is unavailable, install any Python on the supported 3.12
  minor line or set `BA_API_TEST_PYTHON=/absolute/path/to/python3.12`. The
  bootstrap patch need not match `.python-version`; pinned `uv` downloads the
  exact managed project runtime when it is not already cached.
- If managed Python installation fails, preserve the `uv python install`
  diagnostic and verify outbound HTTPS access and user-cache permissions.
- If `uv.lock` is stale, either restore the matching reviewed manifest/lock
  pair or intentionally refresh the lock using the documented update workflow.
  Re-running `./run-tests` will not repair a stale lock.
- If frozen synchronization fails, preserve the error and verify normal PyPI
  access and filesystem permissions. A readiness marker is written only after
  `uv sync --check` proves exact parity, so the next run safely retries.
- Browser installation remains separate from dependency-only checks. Use the
  dependency-contract checker and unit/collection commands directly when no
  browser or deployment access should occur.

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

That selection includes safe unauthenticated checks and authenticated user
reads. It excludes authenticated heartbeat refresh, OAuth state or session
creation, logout, and internal-revocation scenarios.

Mutation and destructive policy is applied during collection, before client or
session fixtures can run. Public-route tests use the unauthenticated client and
do not acquire a user session.

The `authentication` matrix sends one bodyless, queryless request per checked-in
OpenAPI operation and requires the documented `401` `UNAUTHORIZED` JSON response.
Its operation-id parameter names remain directly selectable, and the matrix does
not log in or provision either browser identity. `GET` cases are read-only and
production-safe; every other method remains blocked by production mutation
policy even though authentication should reject it before request parsing.

The same selection also checks unauthenticated `GET /auth/v1/user` and
`GET /auth/v1/session` through the public gateway. Both require the generic
`401` `UNAUTHORIZED` / `Authentication required` JSON response, use no session
fixture, and are read-only and production-safe. Session Gateway publishes its
own checked-in OpenAPI contract, so its operation markers use the explicit
`session_gateway` namespace and cannot collide with unified `/api` operation
ids.

Machine-endpoint coverage lives under `tests/api/session_gateway/`. It checks
missing and plausible invalid cookies, valid user semantics, invalid-cookie
clearing, and authenticated heartbeat refresh followed by a still-valid user
read. Heartbeat is intentionally a `mutation` despite using `GET`. Unknown
`/auth/v1/...` paths and wrong methods are tracked as gateway-routing
regressions rather than OpenAPI operations.

The same suite owns the browser-visible OAuth and logout lifecycle. Environment
YAML declares only non-secret browser contract data: callback/default/logout
paths, accepted identity-provider DNS suffixes, the provider logout path, and
the expected SameSite policy. Browser-backed lifecycle tests are available only
in `browser_preprovisioned` mode. They first preserve the reusable primary
session, then use the same configured identity to create one additional opaque
session for a single scenario and explicitly log out that exact session.
`supplied_sessions` runs skip these scenarios because an opaque cookie cannot
prove ownership of a newly created session.

Lifecycle coverage checks the authorization-code/OIDC/PKCE redirect without
following it, observes the same-origin callback without retaining code or state
values, and validates default, safe, external-absolute, and protocol-relative
return URL behavior. A successful flow requires the public cookie name plus
`HttpOnly`, `Secure`, and the configured SameSite value. A Spring `SESSION`
cookie may remain an internal framework detail, but it is never selected as the
public authentication contract.

Logout stops at the first identity-provider redirect. It requires the reviewed
OpenAPI `302`, an allowlisted HTTPS provider `/v2/logout` endpoint, the configured
same-origin return target, and a correctly scoped public-cookie clearing header.
Fresh clients then reuse the pre-logout cookie against both user and heartbeat
routes and require schema-backed `401` responses, proving server-side deletion.
The unauthenticated logout path is checked separately as an idempotent
redirect-and-clear operation. OAuth success is marked `mutation`; logout is
marked `destructive`, so these scenarios remain excluded by production policy.

Browser observations retain only method, response status, same-origin path, or
an external host classification. Request artifacts redact redirect `Location`
headers and OAuth query values such as code, state, nonce, client id, and PKCE
challenge. They never record passwords, provider tokens, or session cookies.
Each completed same-origin rate-limited browser response must also match a
request-stage pacing reservation; an unmatched callback fails the flow as a
pacing regression before later requests can exhaust ingress capacity.

The Session Gateway snapshot requires `GET /logout` to return `302 Found` with
a URI-formatted `Location` header for the identity-provider logout endpoint.
The offline checker rejects a stale `200` declaration.

Session Gateway's `DELETE /internal/v1/sessions/users/{userId}` operation is not
reachable through public ingress and accepts mesh traffic only from the
permission-service workload identity. This repository will not add pod-specific
clients, forge or delegate that identity, or introduce a trusted
service-to-service runner. The operation is therefore untestable in this
harness's current execution boundary and must be reported as such, not silently
omitted or treated as covered by an ingress-denial request.

The optional public proof is deliberately different: create an active session
for a disposable target identity, use a separately approved admin identity to
call `deactivateUser`, then require both the target's user and heartbeat requests
to return `401`. That would prove the deployed deactivation-to-revocation
workflow, but it would not isolate the internal hop and must not change the
internal case from `untestable`. The required admin identity and disposable-user
cleanup model do not exist yet, so `deactivateUser` remains in the unchanged
admin/global deferral manifest.

The current reviewed matrix is:

| Coverage layer | Covered | Deferred | Untestable | Meaning |
| --- | ---: | ---: | ---: | --- |
| Session Gateway OpenAPI operations | 3 | 0 | n/a | Every operation in the filtered public snapshot has a non-placeholder marker. |
| Inbound behavior scenarios | 14 | 11 | 9 | Every currently executable public case has a marker; dependency/time cases and non-public boundaries remain explicit. |

The 34 scenarios span 14 known inbound routes: five public routes, one
mesh-internal route, four deployment-operational routes, and four
development-only routes. Accurate status is therefore “all known inbound routes
inventoried” and “all currently executable public cases covered,” with 11 cases
deferred and 9 untestable by the current boundary. It is not full Session
Gateway behavior coverage.

## Contract Snapshots And Coverage

The intentionally refreshed unified public contract is checked in at
`schemas/openapi.json`. The separately generated, filtered Session Gateway
contract is checked in at `schemas/session-gateway-openapi.yaml`, and its full
inbound route/case inventory is
`schemas/session-gateway-inbound-inventory.yaml`. One command validates both
contract namespaces, every route/case classification, and the existing admin
and forbidden-response manifests. It writes
`artifacts/openapi-coverage.json` and
`artifacts/session-gateway-coverage.json`:

```bash
.venv/bin/python tools/check-openapi-coverage.py --env local
```

The Session Gateway artifact is a structured report with separate
`operation_coverage` and `scenario_coverage` sections. Each section includes
stable rows plus classification counts and sorted ids. Its `claims` object
states whether all executable public scenarios have markers and keeps
`full_session_gateway_behavior_coverage` false while any scenario is deferred,
untestable, or missing executable evidence. The command prints the same
classifications, counts, ids, and truth flags.

The full `./run-tests` gate fetches the unified public OpenAPI document and reports
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

The Session Gateway snapshot is generated outside this harness from the
service-local Springdoc route and filtered to remove `/internal/*`. From this
repository root, an operator with the documented orchestration/Kubernetes
prerequisites refreshes exactly that reviewed output with:

```bash
repo_root=$(git rev-parse --show-toplevel)
../orchestration/scripts/repo/generate-unified-api-docs.sh \
  --session-gateway-only "$repo_root/schemas/session-gateway-openapi.yaml"
```

Review the resulting diff, especially the logout redirect contract, before
accepting it. Test tools never invoke `kubectl`, read a service-local docs
route, or refresh this snapshot automatically.

Unified markers keep the compatible one-argument form. Session Gateway markers
must provide the contract qualifier:

```python
@pytest.mark.openapi("listViews")
@pytest.mark.openapi("getCurrentUser", contract="session_gateway")
@pytest.mark.session_gateway_case("current-user-authenticated")
```

The route inventory is behavior-level accounting, distinct from operation
markers. Each case is classified as `covered`, `deferred` with one reason and
prerequisite, or `untestable` with one enforced architectural boundary and no
planned prerequisite. Public-ingress denial of an `/internal/...` path does not
count as execution of the mesh-only controller.
Literal `session_gateway_case` markers bind executable tests to covered case
ids, and `artifacts/session-gateway-coverage.json` reports their marker counts.

Response-level security coverage is distinct from operation-marker coverage.
The snapshot must provide schema-backed `401` and `403` responses for every
operation, and the typed request-case inventory must materialize every path
without generating query values or request bodies. A documented `403` is not
treated as live-covered unless a safe principal and reproducible request exist.
All current `403` cases are explicitly classified in
`schemas/deferred-forbidden-responses.yaml`; the eight admin/global operations
remain deferred regardless of HTTP method. See
[docs/openapi-coverage.md](docs/openapi-coverage.md) for the coverage policy.

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
format. Together with centralized per-policy gateway pacing, this keeps the
full suite within the public ingress contract without retries,
environment-specific exceptions, or weakened assertions.

## Markers And Focused Regression Runs

An OpenAPI marker has exactly one operation id:
`@pytest.mark.openapi("operationId")`. Behavioral markers are independent:
`readonly`, `mutation`, `destructive`, `authentication`, `authorization`,
`production_safe`, `auth_smoke`, and `secondary_auth_smoke`. Use normal pytest
marker expressions; the harness does not inspect marker-expression strings to
change test behavior.

Run the complete authentication and user-level authorization selections with:

```bash
./run-tests -m authentication
./run-tests tests/api/authorization/test_cross_user_resources.py
```

The authorization selection preserves privacy-safe cross-user `404` responses
and domain `422` responses; it does not count them as live `403` coverage. All
documented `403` responses remain in the checked-in deferral inventory until a
separate authorization plan defines safe restricted and admin principals.

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
`artifacts/runs/`. Full runs contain `dependency-inventory.json`, which records
the Python and `uv` versions, lock SHA-256 identity, universal locked graph,
editable first-party project, selected installed third-party set, and a
successful exact `uv sync --check` parity result. They also contain unified
OpenAPI coverage, the structured Session Gateway operation/scenario coverage
report, unified live-drift evidence, JUnit pytest results, and redacted public
request diagnostics. Focused runs contain their JUnit result and any request
diagnostics. The harness does not persist browser screenshots, traces, storage,
or provisioning form captures. Artifacts never contain package-index credentials,
passwords, authorization headers, OAuth code or state values, email values, or
session-cookie values.

The dependency-evidence workflow uploads exactly one separate seven-day
artifact. Its semantic allowlist includes setup diagnostics, runtime and tool
metadata, the hashed lock export and selected package list, installed
inventory, dependency tree, Playwright-owned browser revisions, CycloneDX
SBOM, machine- and human-readable `pip-audit` results, scanner status/stderr,
exact parity, diagnostics, and completion. See
[docs/dependency-contract.md](docs/dependency-contract.md) for exact filenames
and failure interpretation.

## Spring Upgrade Workflow

During the Spring Boot upgrade, run the same checked-in tests and snapshot
against the upgraded local services. Treat a failure as a regression to
investigate before changing an assertion. Refresh `schemas/openapi.json` only
through a separate explicit review when the public contract change is
intentional; do not make the suite accept an unexplained service change.

## Quality Gates

```bash
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
