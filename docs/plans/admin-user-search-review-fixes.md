# Admin User Search Review Fixes

Fix the admin user-search tests so text-filter assertions inspect every matching page and
timestamp sort assertions compare parsed instants. Keep the work inside this black-box test
harness, preserve public-gateway request behavior and pacing, and add deterministic regression
coverage for both review findings. No API contract, service implementation, deployment, schema,
or permission changes are in scope.

## Phase 1: Make User Search Assertions Population- and Representation-Safe

### Workspace

.

### Goal

Eliminate false failures caused by expected users appearing after the first search page or by
chronologically ordered timestamps using different fractional precision or UTC-offset
representations.

### Scope

Update `tests/api/users/test_admin_users.py` to traverse all pages for unscoped text-filter
membership assertions and to parse `createdAt`, `updatedAt`, and `deactivatedAt` before checking
sort order and ties. Add focused deterministic tests under `tests/unit/users/` that reproduce both
regressions without contacting a deployed environment.

### Non-goals

Do not change the user API contract, OpenAPI snapshots, service code, deployment configuration,
gateway pacing, authentication setup, environment permissions, exact ID or IdP-subject search
semantics, or the existing explicit pagination-metadata scenario. Do not treat a larger page size
as a substitute for complete traversal. Do not add test-level sleeps, retries, or direct internal
service access.

### Required context

- `AGENTS.md`
- `tests/api/users/test_admin_users.py`
- `tests/unit/auth/test_auth_smoke.py` for the existing pattern of deterministically exercising
  helpers from API test modules
- `schemas/openapi.json`, especially the `getUsers` pagination and sort parameters
- `docs/openapi-coverage.md`, especially the documented `getUsers` coverage guarantees
- The two review findings motivating this plan: expected identities may occur after the default
  first page, and RFC 3339 timestamp strings with different precision are not chronologically
  sortable as raw text.

### Execution steps

1. Add a typed helper in `tests/api/users/test_admin_users.py` that starts at page zero, applies an
   explicit stable `id` sort, follows the response pagination metadata through every matching
   page, and returns the aggregate user records. Keep all requests routed through `_search_users`
   and `GatewayClient` so schema checks, ingress routing, and shared API pacing remain intact.
2. Use the complete result set for the case-insensitive email and display-name checks, the
   multi-term email and display-name OR checks, and the escaped-wildcard absence check. Preserve
   the existing single-page assertions for exact ID and IdP-subject queries whose metadata must
   report exactly one result.
3. Separate timestamp sort fields from ordinary string sort fields. Parse every non-null
   `createdAt`, `updatedAt`, and `deactivatedAt` value into a timezone-aware `datetime` before
   checking ascending or descending order and before deciding whether two adjacent values are a
   tie. Preserve existing null treatment and the requested ID tie-breaker behavior.
4. Add deterministic unit coverage under `tests/unit/users/`. Import the API test module using the
   established import-module pattern, mock its page-search boundary, and prove that an expected
   identity on a later page is included and that complete-result absence checks cannot stop after
   page zero. Assert the requested page numbers and stable sort parameters so increasing only the
   first-page size cannot satisfy the regression.
5. Add timestamp regression cases for all three timestamp fields. Include chronological values
   such as `2026-02-01T10:00:00.123Z` followed by
   `2026-02-01T10:00:00.123456Z`, plus equal instants represented with different fractional
   precision or offsets, and verify that parsed-instant ties use the ID tie-breaker in both sort
   directions.
6. Keep documentation unchanged unless implementation reveals that the durable coverage statement
   in `docs/openapi-coverage.md` is no longer accurate. The intended fix should make its existing
   claim that assertions do not rely on a fixed global user total accurate without changing the
   public behavior being documented.

### Implementation notes

The traversal helper should copy and augment caller query parameters rather than mutate shared
dictionaries. It must advance using returned pagination metadata and remain finite when the result
set is empty. A deterministic sort is necessary so page boundaries are reproducible, but page
traversal remains mandatory regardless of the chosen page size. Avoid asserting a fixed global
user count because reusable and pre-provisioned identities can coexist with unrelated accounts.

Timestamp parsing must happen before both ordering and equality checks. This ensures different
serializations of the same instant are treated as ties and that fractional precision does not
change chronological ordering. Keep original timestamp strings on `_UserRecord` because they are
also reused as exact filter boundaries; conversion is needed only for sort assertions.

### Validation

Run focused deterministic coverage first:

```bash
.venv/bin/python -m pytest tests/unit/users --env local
```

Then run the applicable offline quality gates:

```bash
.venv/bin/python -m pytest tests/unit --env local
.venv/bin/python -m pytest tests/api --env local --collect-only
.venv/bin/python -m pytest tests/api --env production \
  -m "readonly and production_safe" --collect-only
.venv/bin/python -m ruff format --check tests/api/users/test_admin_users.py tests/unit/users
.venv/bin/python -m ruff check tests/api/users/test_admin_users.py tests/unit/users
.venv/bin/python -m mypy src tests
```

When the documented local HTTPS trust, browser dependencies, credentials, and deployment are
available, run the affected live module through the supported public path. Treat this selected run
as partial validation:

```bash
./run-tests tests/api/users/test_admin_users.py
```

Finally run the no-selector local workflow for complete live validation when its prerequisites are
available:

```bash
./run-tests
```

If live prerequisites are unavailable, report that stop condition explicitly; do not weaken TLS,
authentication, ingress routing, pacing, or environment policy to obtain a result.

### Completion criteria

- Every unscoped user text-filter membership or absence assertion evaluates all reported matching
  pages with stable traversal and no fixed-population assumption.
- `createdAt`, `updatedAt`, and `deactivatedAt` order and tie checks operate on timezone-aware
  parsed instants, while non-timestamp fields retain their existing semantics.
- Deterministic tests fail against the original first-page-only and lexical-timestamp behavior and
  pass with the fixes.
- The focused unit tests, full deterministic suite, both API collection gates, Ruff checks, and
  strict mypy pass.
- The affected live module and full local workflow pass when all documented runtime prerequisites
  are present, or any unavailable live validation is clearly reported without a workaround.
