# OpenAPI Authentication And Authorization Response Plan

Adopt the corrected public OpenAPI contract and turn its authentication behavior
into black-box security regressions. The checked-in snapshot must match the
reviewed deployed contract, every documented API operation must reject an
unauthenticated request with its documented `401` JSON response, and every
documented `403` must remain explicitly deferred until a separate authorization
plan defines safe principals and admin/global-state boundaries.

This is a focused successor to the implemented local API regression baseline.
At plan creation, the orchestration aggregate contains 40 operations and every
operation documents both `401` and `403` with `ApiErrorResponse`. Confirm those
facts against the deployed public document during execution rather than treating
the counts as permanent.

## Reviewed OpenAPI Delta

The current review baseline is the checked-in `schemas/openapi.json`; the accepted
candidate is `../orchestration/docs-aggregator/openapi.yaml`. Their operation
inventory is unchanged: both contain 40 operations with the same operation ids,
methods, paths, and 53 component-schema names. The snapshot refresh must contain
all of the following semantic changes and no unrelated deployment drift:

- Add a schema-backed `401` response to all 40 operations. Each response uses
  `ApiErrorResponse` and the example `UNAUTHORIZED` / `Authentication required`.
- Add the same schema-backed `403` response to the 37 operations that do not
  currently document one. Each example uses `FORBIDDEN` / `You do not have
  permission to perform this action`.
- Normalize the existing `403` responses for `getUsers`, `getUser`, and
  `deactivateUser`: change the description from `Insufficient permissions` to
  `Forbidden` and add the standard `FORBIDDEN` example. These are documentation
  changes only in this plan; all authenticated admin-route work remains deferred.
- Add `exclusiveMinimum: 0` to `CreateTransactionRequest.amount`.
- Correct `getExchangeRates` `400` from an array of `ExchangeRateResponse` to
  `ApiErrorResponse`.
- Change `createFormat` `400` from `Bad Request` to `Validation error` and remove
  its generic `INVALID_REQUEST` example.
- Replace `getFormat` `404`'s generic example with the named `Format Not Found`
  example: summary `Statement format does not exist`, description `Format Not
  Found`, type `NOT_FOUND`, and message `Statement format not found with id: 999`.
- Change `updateFormat` `404` from `Not Found` to `Statement format not found`
  and remove its generic `NOT_FOUND` example.
- Change `createTransaction` `400` from `Bad Request` to `Request validation
  failed` and remove its generic `INVALID_REQUEST` example.
- Replace `batchImportTransactions` `400`'s generic example with the named
  `Validation Failed` example: summary `One or more transactions failed
  validation`, description `Validation Failed`, type `VALIDATION_ERROR`, message
  `Validation failed for 2 field(s)`, and `must not be null` field errors for
  `files[0].transactions[44].amount` and `files[1].transactions[89].date`.
- Change `bulkDeleteTransactions` `400` from `Bad Request` to `Invalid request
  (empty ID list)` and remove its generic `INVALID_REQUEST` example.
- Replace `updateTransaction` `404`'s generic example with the named
  `Transaction Not Found` example: summary `Transaction does not exist or is
  deleted`, description `Transaction Not Found`, type `APPLICATION_ERROR`, and
  message `Transaction not found with id: 999`.
- Change `getUser` `404` from `Not Found` to `User not found` and remove its
  generic `NOT_FOUND` example.
- Update `deactivateUser` to document the already-deactivated-user behavior:
  preserve audit fields and role state, still attempt session revocation, return
  `200` only when revocation succeeds, and allow safe retry after failure. Change
  its `503` description to `Session revocation failed after retry; user was
  deactivated, retry is safe` and remove the generic `SERVICE_UNAVAILABLE`
  example.
- Remove generic error examples from `createView` `400`, `getView` `404`,
  `updateView` `400` and `404`, `getViewTransactions` `404`, and `cloneView`
  `400`. Their response descriptions and `ApiErrorResponse` schemas otherwise
  remain unchanged.

## Decisions And Boundaries

- Work only through the public gateway and `GatewayClient`. The orchestration
  aggregate is review context, not a file-copy source or runtime dependency.
  Refresh `schemas/openapi.json` only from the deployed public `/api-docs`
  document after the corresponding local deployment is available.
- Treat absence of authentication as an edge concern. Send one request for every
  documented operation using its real method and a syntactically concrete path,
  but deliberately do not synthesize request bodies or query data. Authentication
  must reject the request before service-level input validation can run.
- Give each generated case an operation-id-based pytest id. Validate HTTP `401`,
  the documented OpenAPI response schema, a non-HTML response, and error type
  `UNAUTHORIZED`; a single representative route is no longer sufficient.
- Derive the unauthenticated matrix from the checked-in OpenAPI snapshot so new
  operations cannot silently miss the security test. Fail deterministically when
  an operation lacks either documented `401` or `403`, or when a path template
  cannot be materialized safely from its parameter example or primitive schema.
- Keep production fail-safe. Unauthenticated `GET` cases may be `readonly` and
  `production_safe`. Mark non-`GET` cases with the existing mutation/destructive
  policy as appropriate so production collection skips them before creating a
  network-capable client. All cases run in the full local baseline.
- A documented potential `403` does not imply that a route has a safe,
  reproducible forbidden request for the available normal identity. Defer all
  authenticated `403` execution in this plan: ordinary routes lack a
  contract-defined restricted principal, and admin routes remain outside scope
  even when they are read-only.
- Do not obtain an admin session, broaden permissions, invent roles, or reinterpret
  cross-user `404`/`422` anti-enumeration behavior as `403`. Existing ownership
  isolation tests remain authoritative for those cases.
- Preserve shared 8-requests/second pacing, HTTPS verification, redacted request
  artifacts, declarative environments, and lazy authentication. The exhaustive
  unauthenticated matrix must not provision or log in either browser account.

## Phase 1: Accept The Contract And Model Security Cases

### Workspace

.

### Goal

Intentionally accept the corrected deployed OpenAPI responses and add a typed,
deterministically validated inventory for authentication and forbidden-response
cases.

### Scope

The checked-in OpenAPI snapshot, every change in the reviewed delta above,
OpenAPI parsing helpers, a precise deferred `403` manifest, deterministic unit
tests, and documentation of the response-level coverage policy.

### Non-goals

No live endpoint assertions, service changes, generated client, admin identity,
global mutation, or automatic acceptance of orchestration files in this phase.

### Required context

- `AGENTS.md`, `README.md`, and this plan
- `schemas/openapi.json` and `schemas/deferred-admin-operations.yaml`
- `src/api_tests/openapi.py`, `coverage.py`, `openapi_drift.py`, and
  `src/api_tests/tools/openapi_coverage.py`
- `tests/unit/openapi/` and `tests/api/auth/test_edge_auth.py`
- The reviewed `../orchestration/docs-aggregator/openapi.yaml` and the deployed
  local public OpenAPI document, used read-only

### Execution steps

1. Compare the checked-in snapshot, reviewed aggregate, and deployed local public
   document. Confirm the unchanged operation and component-schema inventories,
   every `401`/`403` addition or normalization, the amount constraint, the
   corrected `400`, `404`, and `503` contracts, the user-deactivation semantics,
   and every named, changed, or removed example in the reviewed delta. Do not
   review only the newly added status keys.
2. If the deployed local document does not yet contain the accepted changes, stop
   with that external prerequisite instead of copying a sibling-repository file.
   Once it does, run the intentional refresh tool and inspect the resulting diff.
   Confirm that the normal drift comparator reports no difference against live.
3. Add typed OpenAPI helpers that enumerate operations by documented response
   status and materialize path templates for black-box requests. Resolve each path
   parameter from its OpenAPI example first, then a narrow deterministic fallback
   for integer, UUID, or string schemas. Reject missing, duplicate, mismatched, or
   unsupported path parameters with an error naming the operation and parameter.
   Do not build a general example-data or request-body generator.
4. Add a checked-in deferred-forbidden-response manifest with exact operation id,
   method, path, and a constrained reason. Classify regular operations that have no
   restricted normal-user scenario separately from all admin operations. Include
   read-only and mutating admin operations in the deferred set; this plan executes
   no authenticated admin request.
5. Add unit tests proving that every current operation documents schema-backed
   `401` and `403` responses, every operation can produce one stable request case,
   path substitutions are complete and URL-safe, and the deferred `403` set
   exhausts the documented inventory for this plan. Test useful errors for stale
   manifest method/path entries and unsupported templates.
6. Document the distinction between operation-marker coverage, exhaustive `401`
   execution, reproducible `403` execution, and explicit `403` deferral. Do not
   claim a documented possible response is live-covered when no safe principal or
   request exists.

### Implementation notes

Keep parsing at the decoded-JSON boundary and return frozen typed values to tests.
The test URL needs syntactically valid path values only: an unauthenticated request
must not reach query, multipart, or JSON validation. Preserve OpenAPI paths exactly
and continue composing them as `{origin}{api_base_path}{openapi path}`.

Keep all eight operations in `schemas/deferred-admin-operations.yaml` outside
authenticated execution: `searchTransactions`, `countTransactionsAcrossUsers`,
`create`, `update`, `importLatestExchangeRates`, `getUsers`, `getUser`, and
`deactivateUser`. The earlier read-only candidates `searchTransactions`,
`countTransactionsAcrossUsers`, `getUsers`, and `getUser` are no longer an
executable denial set. They remain deferred with the mutating admin operations
until separate admin identity, authorization, cleanup, and global-state safety
work is approved.

### Validation

```bash
.venv/bin/python tools/refresh-openapi.py --env local
.venv/bin/python -m pytest tests/unit/openapi --env local
.venv/bin/python tools/check-openapi-coverage.py --env local
.venv/bin/python -m pytest tests/api --env local --collect-only
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
```

Run the refresh command only after the public local document exposes the reviewed
change. Inspect `git diff -- schemas/openapi.json` and verify the refresh contains
no unrelated deployment drift.

### Completion criteria

- The snapshot contains every item in the reviewed OpenAPI delta, no unrelated
  change, and matches the live structural contract.
- Deterministic code produces exactly one concrete case for each documented
  operation and rejects incomplete authentication-response documentation.
- Every documented `403` has an exact, validated deferral with a safety/identity
  reason; no authenticated admin request is introduced.
- Unit, collection, coverage, formatting, linting, and typing checks pass without
  contacting service internals.

## Phase 2: Exercise Every Operation Without Authentication

### Workspace

.

### Goal

Replace the single-route unauthenticated smoke test with an OpenAPI-driven live
matrix proving that all public API operations fail closed with the corrected
`401` contract.

### Scope

Public-gateway authentication tests, parameter-level safety markers, relevant
marker-policy tests, and focused local execution of the complete matrix.

### Non-goals

No authenticated `403` scenarios, valid request-body generation, admin behavior,
service-internal calls, retries, or per-test sleeps in this phase.

### Required context

- Phase 1 typed case inventory and refreshed `schemas/openapi.json`
- `tests/api/auth/test_edge_auth.py`, `src/api_tests/pytest_plugin.py`, and
  `pyproject.toml` marker declarations
- `src/api_tests/client.py`, `assertions.py`, `schemas.py`, and `logging.py`
- Marker-policy, schema-assertion, client, and request-logging unit tests
- `environments/local.yaml` and `environments/production.yaml`

### Execution steps

1. Replace the representative `/v1/currencies` authentication test with one
   parametrized case per OpenAPI operation. Load only the checked-in snapshot at
   collection, use operation ids in node ids, and issue the documented method and
   concrete path through `unauthenticated_gateway_client.api_request` without a
   cookie, authorization header, request body, or inferred prerequisite probe.
2. For every response, assert HTTP `401`, non-HTML content, the documented OpenAPI
   response schema, and `UNAUTHORIZED` through the existing error assertion. Keep
   diagnostics operation-specific and secret-redacted.
3. Introduce or refine a declared authentication marker if needed to distinguish
   missing-session checks from two-user authorization tests. Ensure staging policy
   for a missing secondary session cannot skip this matrix, and ensure selecting
   the matrix never acquires primary or secondary browser credentials.
4. Apply safety markers to individual generated cases. Mark `GET` cases read-only
   and production-safe; conservatively mark state-changing methods as mutation and
   destructive where their operation semantics warrant it. Extend marker-policy
   tests to prove production skips unsafe cases before fixture setup while still
   collecting the safe unauthenticated reads.
5. Add deterministic tests for case ids, methods, rendered paths, marker assignment,
   and request composition. Use `httpx.MockTransport`; do not call a deployment or
   identity provider from unit tests.
6. Run the entire authentication matrix against local through the public ingress.
   Diagnose any route that returns validation, routing, HTML, `403`, or another
   status as an operation-specific fail-closed regression rather than weakening the
   case payload or adding a test sleep.

### Implementation notes

Pytest parametrization must remain visible at collection and deterministic from
the committed snapshot. Existing operation markers continue to represent general
behavioral coverage; do not add 40 handwritten decorators or duplicate endpoint
lists merely to satisfy marker counting.

Calls using `POST`, `PUT`, `PATCH`, or `DELETE` intentionally omit otherwise
required input. The security assertion is that edge authentication wins before
request parsing. Production policy remains conservative in case that invariant
regresses.

### Validation

```bash
.venv/bin/python -m pytest tests/unit/openapi tests/unit/pytest_plugin tests/unit/test_client.py --env local
.venv/bin/python -m pytest tests/api/auth --env local --collect-only
.venv/bin/python -m pytest tests/api --env production -m "readonly and production_safe" --collect-only
./run-tests tests/api/auth
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
```

Inspect the focused live result and redacted request artifact to confirm one paced
request per current operation and no browser-login traffic.

### Completion criteria

- Every operation in the accepted snapshot produces and executes one unauthenticated
  public-gateway request locally.
- Every case validates the documented `401` JSON shape and `UNAUTHORIZED` type,
  with operation-specific failures.
- The matrix requires no user session, generated credentials, request bodies,
  retries, or rate-limit exceptions.
- Production collection includes only explicitly safe read-only cases and blocks
  all potentially state-changing cases before client setup.

## Phase 3: Lock Forbidden Deferrals And Integrate The Baseline

### Workspace

.

### Goal

Validate the complete `403` deferral inventory, preserve existing user-level
authorization regressions, and make the full local regression baseline
authoritative without executing admin work.

### Scope

Forbidden-response inventory validation, existing user-level authorization tests,
README and agent guidance, and complete repository validation.

### Non-goals

No authenticated admin request, admin identity, successful admin behavior, global
mutation, permission changes, production permission expansion, or replacement of
cross-user isolation tests.

### Required context

- Phase 1 forbidden-response classification and Phase 2 authentication matrix
- `schemas/deferred-admin-operations.yaml` and the new forbidden-response manifest
- `tests/api/authorization/test_cross_user_resources.py`
- `src/api_tests/auth.py`, `pytest_plugin.py`, `assertions.py`, and `schemas.py`
- `README.md`, `AGENTS.md`, local runner behavior, and all repository quality gates

### Execution steps

1. Validate that every documented `403` has one exact manifest entry and that each
   entry states either the missing contract-defined restricted principal for an
   ordinary route or the explicit admin-work deferral. Do not create a live `403`
   request in this plan.
2. Keep all admin operations deferred even when they are read-only or their intended
   normal-user result is `403`. A later authorization plan must define the identity,
   cleanup, and global-state safety model before removing any admin deferral.
3. Re-run the response-inventory validation so additions, removals, or method/path
   moves force an explicit test or deferral decision. Confirm existing cross-user
   tests still expect privacy-preserving `404` or domain `422` responses and remain
   unchanged unless the public contract itself says otherwise.
4. Update README and AGENTS where implemented marker names, focused commands,
   response-coverage rules, or safety boundaries changed. Explain that all `401`
   responses are executable while all `403` responses remain in the checked-in
   deferral inventory for this plan.
5. Run the existing authorization tests, then the complete deterministic,
   collection, quality, and live local gates. Treat selectors as partial
   validation; acceptance requires a no-argument `./run-tests` after the deployed
   OpenAPI matches the refreshed snapshot.

### Implementation notes

A normal user's successful access to ordinary endpoints does not exercise their
documented potential `403`, and a cross-user lookup intentionally returning `404`
does not do so either. Keep that distinction visible in names and documentation.
The standard `FORBIDDEN` examples in the accepted OpenAPI document define response
contracts; they do not by themselves provide a safe live scenario.

### Validation

```bash
PYTHONPATH=src .venv/bin/python tools/check-dependency-contract.py \
  --uv .dependency-tools/uv-0.13.0/bin/uv
.venv/bin/python -m pytest tests/unit --env local
.venv/bin/python -m pytest tests/api --env local --collect-only
.venv/bin/python -m pytest tests/api --env production -m "readonly and production_safe" --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
actionlint .github/workflows/*.yml
shellcheck run-tests
./run-tests
```

### Completion criteria

- Every documented `403` is explicitly deferred with a precise reason, and stale
  classifications are rejected deterministically.
- Existing ownership isolation and production restrictions remain intact; no admin
  request, admin identity, or global mutation was introduced.
- Documentation matches the implemented commands, markers, coverage semantics, and
  safety limits.
- All repository quality gates and the complete no-selector local regression pass
  against the corrected deployed contract.
