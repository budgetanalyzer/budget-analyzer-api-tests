# Review Regression Fixes Plan

Fix the four review findings with small changes to the existing code and focused
regression tests. This supplements the implemented
[local baseline plan](simple-local-api-regression-baseline-plan.md).
Preserve public-gateway access, shared 8-request/second pacing, lazy independent
accounts, secret-safe diagnostics, and production policy. No new framework,
authentication refactor, service changes, or snapshot changes are needed.

## Phase 1: Redact Before Truncating

### Workspace

.

### Goal

Prevent oversized JSON responses from leaking credentials into diagnostics.

### Scope

Response-body redaction and its regression tests.

### Non-goals

Changing the artifact schema or introducing a new redaction library.

### Required context

`AGENTS.md`, `src/api_tests/logging.py`,
`tests/unit/auth/test_auth_helpers.py`, and `docs/local-regression-baseline.md`.

### Execution steps

1. Parse the complete JSON response, apply the existing recursive redactor,
   serialize it, then limit the sanitized prefix to 4,096 UTF-8 bytes. For
   non-JSON or malformed JSON, apply the existing text fallback before truncation.
2. Add oversized-response regressions covering a JSON cookie field and a preview
   token crossing the original truncation boundary. Check artifact output and
   failure context for leaked secrets or token prefixes, and check the byte limit.
3. Document the sanitize-before-truncate behavior in the baseline documentation.

### Implementation notes

Keep the existing helpers and response logging flow. The logger already reads
the complete response; streaming machinery is unnecessary.

### Validation

Run `.venv/bin/python -m pytest tests/unit/auth/test_auth_helpers.py --env local`.

### Completion criteria

Oversized valid JSON uses field-aware redaction, credentials are absent from
both diagnostic outputs, and sanitized body prefixes remain bounded.

## Phase 2: Validate Date And Timestamp Formats

### Workspace

.

### Goal

Reject response strings that violate documented `date` and `date-time` formats.

### Scope

The existing schema validator, its runtime dependency, and schema unit tests.

### Non-goals

Custom date parsers or duplicate validation in individual API tests.

### Required context

`AGENTS.md`, `src/api_tests/schemas.py`, `pyproject.toml`,
`tests/unit/openapi/test_schema_assertions.py`, `schemas/openapi.json`, and
`docs/local-regression-baseline.md`.

### Execution steps

1. Pass a `jsonschema.FormatChecker` to `Draft202012Validator` and declare
   `jsonschema[format-nongpl]` as the runtime dependency so timestamp checking is
   available in fresh installations. Install the updated dependencies in `.venv`.
2. Add parameterized regressions against the checked-in `getTransactions`
   response schema: valid dates and timezone-aware timestamps pass; malformed
   `date`, `createdAt`, and `updatedAt` values each fail independently. Include
   an impossible calendar date and a timestamp missing its timezone.
3. Document that response validation enforces documented date/time formats.

### Implementation notes

Use the library's built-in checker and retain current validation diagnostics.

### Validation

Run `.venv/bin/python -m pytest tests/unit/openapi/test_schema_assertions.py --env local`.

### Completion criteria

Malformed date/timestamp strings fail schema validation; valid examples pass.

## Phase 3: Preserve Pacing After Oversleep

### Workspace

.

### Goal

Keep consecutive pacer releases at least 125 ms apart when sleep overshoots.

### Scope

`RequestPacer.wait` and its deterministic timing tests.

### Non-goals

Retries, test-level sleeps, or a replacement scheduler.

### Required context

`AGENTS.md`, `src/api_tests/client.py`, `tests/unit/test_client.py`, and
`docs/local-regression-baseline.md`.

### Execution steps

1. Keep the existing lock and wait calculation. Set the next deadline from a
   fresh monotonic reading after waiting, plus the minimum interval.
2. Add a fake-timing regression where sleep overshoots by 200 ms. Three calls
   starting at zero must return at approximately 0, 0.325, and 0.650 seconds,
   with no immediate catch-up release. Retain shared-client/browser pacing tests.
3. Document that late wake-ups do not permit catch-up bursts.

### Implementation notes

Keep the single process-wide pacer and the existing injected clock/sleep hooks.

### Validation

Run `.venv/bin/python -m pytest tests/unit/test_client.py --env local`.

### Completion criteria

Normal and oversleep timing tests pass, and shared pacing remains unchanged.

## Phase 4: Verify Both Authentication Smokes And Run Quality Gates

### Workspace

.

### Goal

Make both selectable authentication smokes detect invalid supplied sessions.

### Scope

The two smoke tests, deterministic regressions, documentation, and final checks.

### Non-goals

Changing session acquisition, broadening production markers, or running staging
or production tests against live targets.

### Required context

`AGENTS.md`, `tests/api/auth/test_auth_smoke.py`, `src/api_tests/pytest_plugin.py`,
`src/api_tests/assertions.py`, `tests/unit/auth/test_auth_helpers.py`,
`environments/local.yaml`, `environments/production.yaml`, `README.md`, and
`docs/local-regression-baseline.md`.

### Execution steps

1. Use `gateway_client` in the primary smoke and `secondary_gateway_client` in
   the secondary smoke. Each calls `raw_request("GET", "/auth/v1/user")` and
   asserts HTTP 200 with the existing safe status assertion.
2. Add deterministic tests exercising the actual smoke functions with mocked
   gateway responses and supplied sessions. For each smoke, verify the endpoint
   and account cookie, accept 200, and reject 401 and redirects. Keep fixtures
   independently lazy and preserve both existing marker names.
3. Update README to state that both smokes verify the gateway session in either
   auth mode. Record validation results in the baseline documentation.
4. Run the quality gates below. Report any live prerequisite or deployment
   failure explicitly; do not weaken assertions or change runtime trust.

### Implementation notes

Two explicit smoke requests are sufficient. Retain browser acquisition's
existing verification and the current production selection policy.

### Validation

```bash
.venv/bin/python -m pytest tests/unit --env local
.venv/bin/python -m pytest tests/api --env local --collect-only
.venv/bin/python -m pytest tests/api --env production -m "readonly and production_safe" --collect-only
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
./run-tests
```

The full local run checks both browser smokes, snapshot coverage, and live
contract drift. Mocked supplied-session tests cover invalid-session outcomes
without identity-provider access or recording real session values.

### Completion criteria

Both smokes perform gateway verification; all four regressions and required
quality gates pass. Documentation records the behavior and validation evidence.
