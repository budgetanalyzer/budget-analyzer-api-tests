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
   documented possible `403` alone is not executable evidence.
4. **Explicit forbidden-response deferral** records every documented `403` by
   exact operation id, method, and OpenAPI path in
   `schemas/deferred-forbidden-responses.yaml`. A regular route is deferred when
   no restricted normal-user scenario is contract-defined. Admin/global routes
   use a separate reason because they require an approved admin identity,
   cleanup, and global-state safety plan.

The current forbidden-response manifest exhausts all documented operations.
This does not claim that those `403` responses are live-covered. Cross-user
ownership checks that intentionally return non-disclosing `404` or `422`
responses remain authorization regressions, but they are not reclassified as
`403` coverage.

Session Gateway operations use the stable `session_gateway` contract key.
Unified markers retain the one-argument default, while Session Gateway markers
must be qualified:

```python
@pytest.mark.openapi("operationId")
@pytest.mark.openapi("getSessionStatus", contract="session_gateway")
```

The contract key and operation id form the identity. An identical operation id
in two contracts produces two coverage records and never merges marker counts.
Unknown contract names, nonliteral arguments, extra keywords, and stale
operation ids fail deterministically.

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

An optional public end-to-end proof remains possible only after a separate
safety decision: provision a disposable target identity with an active session,
use a separately approved admin identity to call the public permission-service
`deactivateUser` operation, and require both `/auth/v1/user` and
`/auth/v1/session` for the target session to return `401`. This proves the
deactivation-to-revocation workflow, not the isolated internal hop. Adding that
scenario must leave `internal-user-session-revocation` classified as
`untestable`; the admin identity plus disposable-target lifecycle and cleanup
model is a prerequisite only for the optional public scenario.

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
| Inbound behavior scenarios | 14 | 11 | 9 | 34 |

The scenarios cover 14 known inbound routes by inventory: five public, one
mesh-internal, four deployment-operational, and four development-only. All 14
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
| `heartbeat-account-inactive` | Session deletion, cookie clearing, and `401` for a deactivated or missing account. | Disposable target identity plus approved admin deactivation and cleanup safety model. |
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

Account deactivation, permission-service and Redis faults, refresh races, and
natural TTL expiry remain deferred under their inventory prerequisites. The
suite does not manipulate internal state or wait for the configured TTL.

Session Gateway execution is also subject to the Istio auth-sensitive ingress
descriptor, independently of the NGINX API rate policy. The shared client and
browser infrastructure classifies the exact local auth route families, waits a
full 60-second discrete refill before first use, and accounts for every 10-token
refill process-wide. This intentionally lengthens focused execution. A `429`
with `x-local-rate-limit: auth-sensitive` is not retried; it indicates either a
harness pacing regression or concurrent traffic consuming the deployment-owned
bucket.

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
rejects any completed auth-sensitive browser response that bypassed pacing.

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

The eight admin/global operations in `schemas/deferred-admin-operations.yaml`
remain outside authenticated execution: `searchTransactions`,
`countTransactionsAcrossUsers`, `create`, `update`,
`importLatestExchangeRates`, `getUsers`, `getUser`, and `deactivateUser`. This
includes their read-only operations; HTTP method alone does not make an admin
request safe.

Run the offline validation with:

```bash
.venv/bin/python -m pytest tests/unit/openapi --env local
.venv/bin/python tools/check-openapi-coverage.py --env local
```

The coverage command validates both deferred manifests against the selected
OpenAPI inventory. Stale methods or paths, duplicate entries, missing
deferrals, unsupported path templates, and incomplete response documentation
are deterministic failures. It also requires every operation in the admin
deferral inventory to use `admin_identity_and_safety_plan_required` and every
other operation to use `no_restricted_normal_user_scenario`; moving an
operation across that boundary requires an explicit update to both manifests.
It also writes the stable Session Gateway case artifact and validates the
Session Gateway snapshot and inventory regardless of whether the unified source
is the snapshot or live public document.

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
