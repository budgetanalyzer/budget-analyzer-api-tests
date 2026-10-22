# API Contract And Coverage Policy

The checked-in `schemas/openapi.json` is the reviewed unified public contract.
`schemas/session-gateway-openapi.yaml` is a separate reviewed contract generated
and filtered by orchestration. The latter is a snapshot, not a runtime sibling
dependency: this harness never invokes Kubernetes or reads a service-local docs
route.

Coverage has four separate meanings:

1. **Operation-marker coverage** associates handwritten behavior with an exact
   OpenAPI `operationId`. It does not imply that every response status has been
   exercised.
2. **Exhaustive unauthenticated coverage** uses one typed, concrete request case
   per documented operation. Every operation must document schema-backed `401`
   and `403` responses before this inventory can be built. Path parameters use
   their documented examples first, then only deterministic integer, UUID, or
   string fallbacks. Query parameters and request bodies are deliberately
   omitted because gateway authentication must reject the request first.
   The live `authentication` matrix uses operation ids as pytest case ids and
   validates HTTP `401`, non-HTML JSON, the documented response schema, and error
   type `UNAUTHORIZED`. It uses only the unauthenticated gateway client.
3. **Reproducible forbidden-response coverage** requires an authenticated
   principal and request that the public contract defines as forbidden. A
   documented possible `403` alone is not executable evidence. A literal
   `@pytest.mark.forbidden_response("operationId")` associates that evidence
   with one unified-contract operation. It does not count as operation-success
   coverage.
4. **Explicit forbidden-response deferral** records every documented `403` by
   exact operation id, method, and OpenAPI path in
   `schemas/deferred-forbidden-responses.yaml` when executable evidence does not
   exist. A regular route is deferred when no restricted normal-user scenario
   is contract-defined. Unsafe global writes use
   `global_mutation_not_authorized`; admin scenarios that still need an approved
   ownership or safety model remain explicit.

The checker requires an exact partition for all 40 documented `403` responses:
five have executable normal-user denial evidence and 35 have exact deferrals.
An operation may never appear in both groups or neither group. Unknown markers,
placeholder-only evidence, duplicate deferrals, stale method/path entries, and
partition gaps fail deterministically. Cross-user ownership checks that
intentionally return non-disclosing `404` or `422` responses remain
authorization regressions, but they are not reclassified as `403` coverage.

The five covered responses are `searchTransactions`,
`countTransactionsAcrossUsers`, `getUsers`, `getUser`, and `deactivateUser`.
Their tests use the validated primary normal identity; the detail denial
derives its path value from that identity instead of targeting an unrelated
account, and the deactivation denial targets only the fresh execution-owned
disposable user. All five operations also have successful admin behavior
coverage and are no longer operation-deferred. Denial evidence and successful
behavior remain independently asserted.

Session Gateway operations use the stable `session_gateway` contract key.
Unified markers retain the one-argument default, while Session Gateway markers
must be qualified:

```python
@pytest.mark.openapi("operationId")
@pytest.mark.openapi("getSessionStatus", contract="session_gateway")
@pytest.mark.forbidden_response("getUsers")
```

The contract key and operation id form the identity. An identical operation id
in two contracts produces two coverage records and never merges marker counts.
Unknown contract names, nonliteral arguments, extra keywords, and stale
operation ids fail deterministically.

`artifacts/forbidden-response-coverage.json` reports response-level counts,
sorted ids, and rows separately from `artifacts/openapi-coverage.json`. Each row
contains the resolved snapshot method/path, executable and placeholder marker
counts, status, and optional deferral reason. The CLI prints the same covered
and deferred counts and ids.

The Session Gateway snapshot declares `GET /logout` as `302 Found` and requires
the `Location` header schema to be a URI-formatted string. Snapshot validation
rejects a missing `302`, a stale `200`, or a weakened header schema through the
normal offline coverage workflow.

The mesh-internal
`DELETE /internal/v1/sessions/users/{userId}` revocation operation has no public
ingress route, and mesh authorization admits only the permission-service
workload identity. This public-gateway harness does not run pod-specific clients,
mint or delegate workload identities, or run a trusted service-to-service
client. No direct internal-route test lane is planned. Internal operations must
be identified as untestable through this harness rather than omitted or counted
as covered by an external denial or a service repository's local integration
test.

The exact-local public end-to-end proof provisions a fresh execution-owned
disposable target with two active sessions, uses the separately validated admin
identity to call the public permission-service `deactivateUser` operation, and
replays both original cookies against `/auth/v1/user`, a protected API read, and
`/auth/v1/session`. This proves the deactivation-to-revocation workflow, not the
isolated internal hop. `internal-user-session-revocation` therefore remains
classified as `untestable`.

## Session Gateway Route And Case Inventory

`schemas/session-gateway-inbound-inventory.yaml` records controller, Spring
Security, Actuator, and Springdoc ownership for every inbound route. Exposure is
one of public, mesh-internal, deployment-operational, or development-only.
Stable behavior-case ids describe observable contracts independently of Java
class names.

Every case has exactly one status:

- `covered` identifies deterministic behavior assigned to the supported public
  black-box lane.
- `deferred` requires a nonempty reason and the concrete prerequisite that
  would make the behavior deterministic. Dependency faults, account-state
  transitions, TTL expiry, and refresh races remain here until their named
  deployment or identity control exists.
- `untestable` requires a nonempty reason and enforced architectural boundary;
  it cannot carry a prerequisite that makes it look like planned work. The
  mesh-only revocation route, non-public Actuator routes, and service-local
  Springdoc/Swagger routes use this classification.

The reviewed baseline is:

| Inventory layer | Covered | Deferred | Untestable | Total |
| --- | ---: | ---: | ---: | ---: |
| Filtered Session Gateway OpenAPI operations | 3 | 0 | n/a | 3 |
| Inbound behavior scenarios | 15 | 11 | 9 | 35 |

The scenarios cover 14 known inbound routes by inventory: five public, one
mesh-internal, four deployment-operational, and four development-only. All 15
currently executable public scenarios have literal test markers. The 11
deferred scenarios have one concrete reason and one enabling prerequisite each;
the 9 untestable scenarios name an enforced boundary and carry no planned
prerequisite. This supports “all known inbound routes inventoried” and “all
currently executable public cases covered.” It does not support “full Session
Gateway coverage.”

The deferred gaps are intentionally narrow:

| Cases | Observable outcome retained by the inventory | Enabling prerequisite |
| --- | --- | --- |
| `oauth-authorization-state-store-failure`, `oauth-callback-redis-failure`, `logout-redis-delete-failure`, `current-user-redis-failure`, `heartbeat-redis-failure` | The exact `/oops`, 5xx/500/503, cookie, redirect, and no-session-extension behavior recorded on each case. | Approved Redis fault injection in a controllable test deployment, scoped to test-owned state or sessions. |
| `oauth-callback-idp-failure` | `302` to `/login?error=auth_failed` without a new public session. | Approved identity-provider fault injection in a controllable test deployment. |
| `oauth-callback-permission-service-failure`, `heartbeat-permission-service-failure` | `/oops` without a public session, or JSON `503` without extending the existing session. | Approved permission-service fault injection in a controllable test deployment and scoped test identity. |
| `heartbeat-account-inactive` | Session deletion, cookie clearing, and `401` for a deactivated or missing account whose session still exists. | Controllable account transition that leaves the exact test-owned Redis session present; successful public deactivation bulk-revokes it and cannot isolate this fallback. |
| `heartbeat-refresh-race` | Cookie clearing and `401` when the session disappears during refresh. | Controllable test deployment with a synchronization point scoped to the test-owned session. |
| `heartbeat-natural-ttl-expiry` | Cookie clearing and `401` after natural expiry. | Configurable short-lived test session in a controllable test deployment. |

The untestable boundaries are equally explicit. Direct internal revocation lacks
both public routing and the permission-service workload identity. The four
Actuator cases would require an explicitly approved public operational route.
The four Springdoc/Swagger cases would require a controllable deployment with
those development routes enabled and publicly exposed. Those would be explicit
charter changes, not expected follow-up work for this harness.

The checker rejects duplicate case ids or routes, malformed classifications,
snapshot operations absent from the inventory, inventory operation ids absent
from the snapshot, and stale method/path pairs. Operation-marker coverage and
behavior-case accounting remain separate; one cannot satisfy the other.

Executable behavior uses literal case markers alongside any applicable
namespaced operation marker:

```python
@pytest.mark.openapi("getCurrentUser", contract="session_gateway")
@pytest.mark.session_gateway_case("current-user-authenticated")
```

The checker rejects unknown case ids, markers attached to deferred or
untestable cases, and any `covered` case with no marker. Unsupported public
route and method checks use `gateway_routing` and do not masquerade as OpenAPI
operation coverage.

`artifacts/session-gateway-coverage.json` is an object with separate
`operation_coverage` and `scenario_coverage` sections. Both contain stable rows
and classification entries with counts and sorted ids. The `claims` section
reports inventory completeness, executable-public-case evidence, and a derived
`full_session_gateway_behavior_coverage` flag. That final flag cannot become
true while an operation is missing/deferred/placeholder, an executable case has
no marker, or any scenario remains deferred or untestable. The CLI prints the
same operation and scenario classifications, counts, ids, and claims.

## Session Gateway Machine Endpoints

The dedicated `tests/api/session_gateway/` suite validates missing, invalid,
and valid public sessions for both machine endpoints. All documented responses
are checked against the Session Gateway snapshot. User responses additionally
require an authenticated flag, nonempty subject/name/email, and typed role and
permission arrays. Heartbeat responses require a nonempty internal user id,
typed roles, and a bounded future epoch expiry; a follow-up user read proves
the shared primary session remains valid with consistent roles.

An authenticated heartbeat is marked `mutation` because it refreshes grants,
expiry, Redis TTLs, and the per-user session index. Missing and invalid session
checks remain production-safe reads; only a nonempty stale heartbeat cookie is
required to receive a scoped `Max-Age=0` clearing response. The independent
secondary-account smoke is supporting session-acquisition evidence and does
not add a second operation or behavior-contract claim.

The public admin-deactivation lifecycle now proves bulk revocation. The
heartbeat-specific inactive-account fallback remains deferred because the
successful bulk operation removes both target sessions before heartbeat can
observe an inactive account with a still-present session. Permission-service
and Redis faults, refresh races, and natural TTL expiry remain deferred under
their inventory prerequisites. The suite does not manipulate internal state or
wait for the configured TTL.

Session Gateway execution is subject to the independent Istio login-initiation,
login-completion, and session-operations policies. The shared client and browser
infrastructure classifies their exact bounded route families and paces each
below its sustained allowance process-wide, without modeling Envoy bursts or a
first-use cooldown. The API policy is paced independently. A `429` with
`x-local-rate-limit-policy` is not retried; it indicates either a harness pacing
regression or concurrent traffic sharing the deployment's per-client address.
Canonical policy boundaries and per-ingress-instance semantics live in
`../orchestration/docs/architecture/security-architecture.md`.

## Session Gateway Browser Lifecycle

`tests/api/session_gateway/test_oauth_lifecycle.py` covers the Spring
Security-owned authorization and callback routes together with the
controller-owned logout route. Authorization entry is inspected without
following the provider redirect and requires authorization-code flow, the
configured HTTPS callback, OIDC scopes, nonempty state, and PKCE S256.
Provider-specific client ids and query ordering are deliberately not
snapshotted.

Browser observations are typed and intentionally lossy: they retain only the
HTTP method, response status, same-origin path, or an external host
classification. Callback query values, credentials, provider tokens, and
cookies cannot enter their representations. HTTP request artifacts additionally
redact OAuth query values and every redirect `Location` header.
Browser login failures report redacted diagnostics and suppress the original
Playwright exception chain so tracebacks and JUnit reports preserve that redaction.

Chromium request-stage interception pauses selected-origin browser requests
before network dispatch and reserves them from the same process-wide pacer as
`GatewayClient`. This specifically includes the cross-origin provider redirect
to `/login/oauth2/code/idp`; generic Playwright route handling sees only the
first request in that redirect chain. A safe observation-to-reservation audit
rejects any completed rate-limited browser response that bypassed pacing.

Default and explicit safe-return flows require observed callback traversal,
the final same-origin path, the configured public cookie with `HttpOnly`,
`Secure`, and expected SameSite metadata, and a schema-backed current-user read.
External absolute and protocol-relative return URLs complete the same hosted
flow but must land on the default `/` fallback. The framework cookie name
`SESSION` remains an allowed internal detail and is never accepted as the
public authentication cookie.

Each successful scenario creates one additional session for the already
configured primary identity. It does not replace or invalidate the
session-scoped primary fixture. The test calls no-follow `GET /logout`, validates
the OpenAPI `302`, allowlisted provider endpoint, encoded configured return
target, and clearing-cookie attributes, then reuses the old opaque cookie in
fresh user and heartbeat clients and requires schema-backed `401` responses.
A narrow `finally` cleanup may repeat logout only for that exact owned cookie
after an earlier assertion failure. Supplied-session mode skips these creation
scenarios because it cannot establish ownership of a newly acquired session.

Provider rejection with a consumed test-created state and invalid-state
callback handling are deterministic controlled-failure checks. Callback IdP
transport/token failures, permission synchronization failures, Redis callback
failures, authorization-state persistence failures, and logout Redis deletion
failures remain deferred under the exact prerequisites recorded in
`schemas/session-gateway-inbound-inventory.yaml`.

Safety markers are assigned per generated unauthenticated case. `GET` requests
are `readonly` and `production_safe`; non-`GET` requests are conservatively
`mutation`, with delete, hide, import, deactivation, and membership-removal
semantics also marked `destructive`. Production therefore collects only the
safe reads selected by `readonly and production_safe`, while collection policy
skips unsafe methods before client fixture setup. These generated security cases
do not carry `openapi` markers, so response-security coverage cannot mask a
missing general behavioral test.

Three admin/global operations in `schemas/deferred-admin-operations.yaml` remain
outside successful authenticated execution: currency `create`, currency
`update`, and `importLatestExchangeRates`. These global writes remain untouched,
including negative authenticated requests.

`getExchangeRates` success remains statically inventoried by its operation
marker, but its live success request depends on environment state. A fresh
database guarantees a non-empty configured currency catalog, not an enabled
currency series, and the service rejects disabled targets with `422
CURRENCY_NOT_ENABLED`. The read-only test therefore lists all configured
currencies, fails if that catalog is empty, and selects an enabled target
(preferring non-`USD`). When no series is enabled, it emits a `RuntimeWarning`
stating that the successful exchange-rate path was not tested and then skips
with the same reason. That warning and skip are not successful live execution
of the `getExchangeRates` response contract. The test never enables a currency
or substitutes a disabled target, and the `200` request and response assertions
still execute whenever an enabled series exists.

`searchTransactions` and `countTransactionsAcrossUsers` are covered by
schema-backed admin scenarios over six test-created rows split across distinct
primary and secondary owners. The scenarios use owner ids returned by public
transaction responses and a unique description discriminator, so retained rows
from prior runs cannot change exact result sets. Coverage includes all 15
filters, documented text semantics, inclusive boundaries, combined criteria,
stored cross-currency numeric comparisons, all 11 sort fields in both
directions, default and compound ordering, paging metadata and traversal, empty
pages, and count/search consistency. Unsupported-sort `400`, unauthenticated
`401`, and normal-user `403` behavior have schema-backed executable evidence.
Malformed date and enum bindings also have stable `400 VALIDATION_ERROR` field
evidence, but their scalar `fieldErrors.rejectedValue` currently conflicts with
the shared snapshot's object type; those tests deliberately do not claim schema
conformance for that diagnostic field. The documented `500` and `503`
dependency-failure responses remain gaps because this public harness has no
approved fault-injection control; no direct service or deployment path is
introduced to manufacture them.

`getUsers` and `getUser` resolve the reusable primary, secondary, and admin
identities by exact public IdP subject and then use the canonical internal ids
returned by the public API. Coverage checks every filter, inclusive timestamp
boundaries, case-insensitive and multi-word OR text matching, escaped wildcard
characters, combinations, empty results, all supported sort directions with an
explicit id tie-breaker, exact-filter pagination metadata, invalid bindings,
list/detail consistency, active roles and audit state, and a schema-backed
missing-user `404`. Assertions do not rely on email uniqueness, a fixed global
user total, or database quiescence. The disposable-user lifecycle covers
positive `DEACTIVATED` membership and exclusion from the `ACTIVE` filter.
Dependency-driven `500` and `503` responses still require unavailable fault
controls.

`deactivateUser` uses one fresh exact-local normal target per selected
execution. Before mutation, the target's saved run/scenario provenance is bound
to its public subject and exact canonical id and checked against all reusable
identities. The lifecycle proves normal-user `403` without state change, admin
success and role count, actor/time audit state, two-session public revocation,
idempotent replay with unchanged audit data, and a confirmed-missing target
`404`. A schema-backed `503` is treated as potentially committed partial state:
the harness reads the retained target status for diagnostics and sends no hidden
retry.

Admin statement-format scenarios create one primary-owned and one
secondary-owned `USER` format per execution. The admin can list, get, and
update both, while normal-user ownership visibility and caller-specific hidden
preferences remain intact. Hide and unhide deliberately stay owner-only even
for `statementformats:write:any`; the scenario restores the owner preference
after proving that boundary. Only those run-created formats are updated.
Existing `SYSTEM` formats are read-only: creating a global catalog entry and
updating a seeded `SYSTEM` format remain deferred scenarios until an explicit
catalog data-safety lifecycle exists. General `createFormat` and `updateFormat`
operation markers do not claim those deferred scenarios. Invalid USER-format
updates assert stable `400 VALIDATION_ERROR` fields, but the rejected scalar
values have the same shared `fieldErrors.rejectedValue` object-schema mismatch
documented for malformed transaction query bindings.

User-admin request URLs redact arbitrary `id`, `idpSub`, email, and display-name
query values after URL decoding, user detail path ids, and profile fields in
response diagnostics. Session Gateway current-user diagnostics also redact the
subject and display name. Identity-reference response fields are redacted in
every response. The same response context feeds assertion failures and JUnit
output; tests use non-rendering comparisons for profile consistency so
arbitrary pre-provisioned identity values are not copied into artifacts.

Run the offline validation with:

```bash
.venv/bin/python -m pytest tests/unit/openapi --env local
.venv/bin/python tools/check-openapi-coverage.py --env local
```

The coverage command validates both deferred manifests against the selected
OpenAPI inventory. Stale methods or paths, duplicate entries, missing
deferrals, unsupported path templates, and incomplete response documentation
are deterministic failures. The remaining currency/rate writes use
`global_mutation_not_authorized`; other deferred responses retain their exact
reviewed reason. Moving an operation from deferral to executable evidence
requires an explicit update to the forbidden-response manifest, while successful
operation coverage is accounted for independently in the admin deferral
manifest. The command also writes the stable Session Gateway case artifact and
validates the Session Gateway snapshot and inventory regardless of whether the
unified source is the snapshot or live public document.

Refresh the unified snapshot only through `tools/refresh-openapi.py`. Refresh
the Session Gateway snapshot explicitly from the repository root only after its
service contract has been reviewed:

```bash
repo_root=$(git rev-parse --show-toplevel)
../orchestration/scripts/repo/generate-unified-api-docs.sh \
  --session-gateway-only "$repo_root/schemas/session-gateway-openapi.yaml"
```

This orchestration-owned command requires its documented cluster and tool
prerequisites, fetches `/v3/api-docs` service-locally, filters `/internal/*`, and
writes only the requested YAML output. The route inventory still records the
filtered internal operation as architecturally untestable.
