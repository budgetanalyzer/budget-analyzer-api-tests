# OpenAPI Coverage Policy

The checked-in `schemas/openapi.json` is both the reviewed public contract and
the executable operation inventory. Refresh it only from the deployed public
`/api-docs` route after the deployment change has been reviewed. The sibling
orchestration aggregate is review context, not a snapshot source or runtime
dependency.

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

Session Gateway publishes a separate OpenAPI specification, so its
`/auth/v1/user` and `/auth/v1/session` authentication checks are intentionally
excluded from this unified `/api` inventory. Dedicated black-box cases still
exercise both machine endpoints through public ingress and require the shared
generic `ApiErrorResponse` wire behavior without acquiring a session.

The generated Session Gateway specification currently declares `GET /logout`
with a `200` response, but the public implementation and documented browser
contract return `302 Found` with an identity-provider logout location. The
generated declaration is incorrect; tests must preserve the observable redirect
behavior and must not reinterpret `200` as the accepted logout contract.

The mesh-internal
`DELETE /internal/v1/sessions/users/{userId}` revocation operation has no public
ingress route, and mesh authorization admits only the permission-service
workload identity. This public-gateway harness does not run pod-specific clients,
mint workload identities, or bypass that boundary, and no direct internal-route
test lane is planned. A future public admin deactivation scenario could verify
the end-to-end revocation outcome, but it would not constitute a direct test of
the internal hop. Internal operations must be identified as untestable through
this harness rather than omitted or counted as covered by an external denial.

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
