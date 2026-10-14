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
mint workload identities, or bypass that boundary, and no direct internal-route
test lane is planned. A future public admin deactivation scenario could verify
the end-to-end revocation outcome, but it would not constitute a direct test of
the internal hop. Internal operations must be identified as untestable through
this harness rather than omitted or counted as covered by an external denial.

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

The checker rejects unknown case ids and markers attached to deferred or
untestable cases. The Session Gateway artifact reports `test_marker_count` for
each case. A zero count remains visible because `covered` classifies a
deterministic public behavior assigned to this plan; it does not turn future
phase work into executed evidence. Unsupported public route and method checks
use `gateway_routing` and do not masquerade as OpenAPI operation coverage.

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
