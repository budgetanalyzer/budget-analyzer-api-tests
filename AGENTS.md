# Budget Analyzer API Tests

## Repository Purpose

This repository contains standalone black-box tests for the Budget Analyzer
public gateway API. It tests deployed environments through the same public lane
used by the browser:

```text
test runner -> HTTPS ingress -> ext_authz -> NGINX -> services
```

The suite must stay separate from service implementation repositories and must
not depend on service internals.

## Session Initialization

At the start of work:

1. Read this `AGENTS.md`.
2. Inspect the repository structure before editing.
3. Read the nearest relevant modules, tests, environment files, and plan
   documents before changing them.

Keep changes scoped to the requested plan phase or user request. The active
implementation plan is
`docs/plans/simple-local-api-regression-baseline-plan.md`. Treat the other
plans in `docs/plans/` as historical context when their successor notices say
they are superseded.

## Core Product Constraints

- Keep this a black-box public API test suite. Do not add direct database,
  Redis, RabbitMQ, Kubernetes, service-DNS, or service-internal setup paths.
- Compose API requests as `{origin}{api_base_path}{openapi path}` through
  `GatewayClient`.
- Treat the public NGINX rate policy as fixed: rate-limited API routes share
  one per-client-IP bucket at 10 requests/second with `burst=50` and `nodelay`.
  All `GatewayClient` instances in a process must share deterministic,
  monotonic pacing capped at 8 requests/second. Same-origin gateway requests
  made during browser authentication must reserve slots from the same pacer.
- Treat the OpenAPI document as the executable coverage manifest.
- Keep environment YAML declarative and non-secret. Read secrets only from
  environment variables or CI secret stores.
- Do not write generated passwords, session cookies, authorization headers, or
  other credentials to artifacts.
- Use `browser_preprovisioned` as the primary local and staging auth mode.
  Local identities are generated lazily through public hosted signup and saved
  only in gitignored `.env.local`; staging identities remain pre-provisioned.
  `supplied_sessions` is allowed with opaque `BA_SESSION` values supplied
  through environment variables.
- Production defaults must remain read-only: `allow_mutation: false` and
  `allow_destructive: false`.
- Do not broaden production permissions to reach coverage targets.
- Keep admin/global operations deferred until a separate admin identity,
  cleanup, and global-state safety plan exists.
- Do not add generic cleanup callbacks or janitors. Use the pytest session's
  run id and resources created by the current test as the user-data boundary.

## Implemented Workflow

- Bootstrap and run the full local suite with `./run-tests`. It creates and
  reuses `.venv`, installs dependencies and Chromium when needed, fixes the
  target to the declared local environment, lazily provisions browser
  accounts, checks snapshot coverage and live contract drift, and writes an
  isolated ignored run report. Pass ordinary pytest selectors through this
  command, and treat any run with selectors as partial validation.

- Validate environment configuration with:

  ```bash
  .venv/bin/python tools/validate-environment.py --env local
  ```

- Run deterministic unit tests and offline API collection with:

  ```bash
  .venv/bin/python -m pytest tests/unit --env local
  .venv/bin/python -m pytest tests/api --env local --collect-only
  ```

- Check OpenAPI marker coverage against the checked-in snapshot with:

  ```bash
  .venv/bin/python tools/check-openapi-coverage.py --env local
  ```

  This command also writes `artifacts/openapi-coverage.json` and fails for
  unrecognized markers, missing or placeholder-only non-deferred coverage, or
  stale deferred method/path entries. Keep snapshot refresh as the separate,
  intentional `tools/refresh-openapi.py` operation. The normal no-argument
  `./run-tests` workflow performs coverage and live structural drift checks
  itself; do not require operators to invoke either tool first.

- Live API tests require the selected environment, runtime-provided HTTPS
  trust, Playwright system dependencies, and network access. Exact-local tests
  create and reuse at most one primary and one secondary normal browser account
  in `.env.local`; other environments require pre-provisioned users or supplied
  sessions. Pytest does not run inferred prerequisite probes; failures occur at
  the request, signup, or login that needs the missing prerequisite.
- Production smoke execution requires a supplied read-only primary session and
  must use:

  ```bash
  BA_PRIMARY_SESSION=... .venv/bin/python -m pytest tests/api \
    --env production -m "readonly and production_safe"
  ```

## Python Baseline

Use modern, explicit Python. The implementation should feel like a small typed
system, not loose scripting glue.

- Require Python 3.12 unless the project intentionally changes runtime support.
- Use a `src/` layout for importable code.
- Put tests outside application code under `tests/`.
- Configure packaging, Ruff, mypy, and pytest in `pyproject.toml`.
- Keep development dependencies separate from test-runner execution
  dependencies.
- Prefer standard-library types and modules for orchestration unless the
  repository plan explicitly calls for a test-harness dependency.
- Use `pathlib.Path` for filesystem paths.
- Read and write text with explicit `encoding="utf-8"`.
- Use timezone-aware UTC timestamps for generated ids and serialized state.
- Keep business logic out of CLI/tool entrypoints. Tools should parse
  arguments, call typed functions, print user-facing messages, and return exit
  codes.
- Implement tool entrypoints as `main(argv: Sequence[str] | None = None) -> int`
  where practical.
- Keep generated artifacts out of version control: `.venv/`, `.mypy_cache/`,
  `.ruff_cache/`, `dist/`, `build/`, `*.egg-info/`, `__pycache__/`,
  `.coverage`, `htmlcov/`, `.pytest_cache/`, and generated request or coverage
  artifacts.

## Typing Standards

- Run mypy in strict mode.
- Annotate all public functions, methods, dataclasses, and module-level
  constants where the type is not obvious.
- Prefer precise stdlib types from `collections.abc` such as `Sequence`,
  `Mapping`, `Iterable`, and `Callable` for interfaces.
- Avoid `Any`. If it is unavoidable at external boundaries such as decoded
  YAML, decoded JSON, OpenAPI documents, httpx, Playwright, or pytest hooks,
  isolate it and validate into typed dataclasses or Pydantic models quickly.
- Avoid broad `dict[str, object]` plumbing across modules.
- Use dataclasses or Pydantic models for durable value objects such as
  environment config, sessions, run state, operation coverage, and request
  results.
- Use literals or enums for constrained status values, auth modes, environment
  types, and coverage states.
- Do not silence mypy with broad ignore comments. A narrow ignore must include
  the error code and a short reason.

## Formatting And Linting

Use Ruff as the single formatting, linting, pyupgrade, and import-sorting path.
Do not add Black, isort, Flake8, or autopep8 unless the user explicitly asks
for that tool split.

Expected commands in this workspace use the repository-local virtualenv. If
`.venv/` is missing, create or install the dev environment first rather than
falling back to global tooling.

Run the repository quality gates with:

```bash
.venv/bin/python -m pytest tests/unit --env local
.venv/bin/python -m pytest tests/api --env local --collect-only
.venv/bin/python -m pytest tests/api --env production -m "readonly and production_safe" --collect-only
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
./run-tests
```

Ruff should use `line-length = 100`, `target-version = "py312"`,
`src = ["src", "tests"]`, and lint selections including `E`, `F`, `I`, `UP`,
`B`, `SIM`, and `RUF`.

## Testing Standards

Use pytest for tests.

- Keep deterministic tests under `tests/unit/` and deployed public HTTP tests
  under `tests/api/`. Keep `pytester` enabled only in the unit subtree.
- Prefer `tmp_path`, `monkeypatch`, `capsys`, `httpx.MockTransport`, and
  `respx` for harness unit tests.
- Do not call real identity providers or live Budget Analyzer targets from unit
  tests.
- Keep tests deterministic. Do not add sleeps to individual API tests; public
  gateway rate compliance belongs in the shared `GatewayClient` pacer. Use
  injected fake timing to unit-test pacing. Keep sleeps for timeout-specific
  unit tests short.
- Use OpenAPI markers consistently:

  ```python
  @pytest.mark.openapi("operationId")
  ```

- Keep browser/session smoke outcomes identifiable. Use `auth_smoke` for the
  primary account and `secondary_auth_smoke` for the independently lazy
  secondary account. They run in the full local suite and remain directly
  selectable with ordinary pytest marker expressions.
- Use `placeholder` only while a phase is under construction. Placeholder
  coverage is not an acceptable local regression baseline.
- Mark mutating tests with `mutation`, destructive tests with `destructive`,
  read-only tests with `readonly`, authorization tests with `authorization`,
  and production-safe tests with `production_safe`.
- Production execution should select only explicitly production-safe read-only
  tests.
- Mutation and destructive markers must be blocked by environment policy before
  request fixtures or network-capable clients are set up.
- For pytest configuration, prefer strict config, strict markers, declared test
  paths, and importlib import mode.

## API Execution Safety

- Use `GatewayClient` for API requests so base origin, API prefix, TLS policy,
  timeout, cookies, request logging, and public-gateway pacing stay centralized.
- Preserve the shared 8-requests/second client-side pace as deliberate
  compliance with the public gateway contract. Do not retry HTTP 429 responses,
  add test-level sleeps, bypass NGINX, forge client identity headers, or add
  environment-specific rate-limit exceptions.
- Preserve HTTPS certificate verification for live tests in every environment,
  including local Tilt targets. Do not add insecure runner flags, environment
  switches, `verify=False`, Playwright `ignore_https_errors`, or equivalent
  bypasses for agent containers.
- Treat HTTPS trust and browser system dependencies as runtime prerequisites.
  Do not install trust anchors, invoke external trust helpers, generate or
  rotate certificates, or modify the runtime from this repository. Report a
  request or login failure if the runtime prerequisite is absent.
- Use `raw_request` only for `/api-docs`, auth edge checks, and other explicitly
  public non-API-prefix routes.
- Never seed sessions in Redis, exchange identity-provider tokens directly for
  API access, or bypass the Session Gateway.
- Stream or record enough request details for diagnostics, but redact secrets
  before writing artifacts.
- Keep mutating tests guarded by environment policy before any mutating request
  is made.

## State, Files, And Schemas

- Keep environment files under `environments/` non-secret.
- Keep `.env.local` gitignored and owner-only. Preserve complete credential
  pairs and provisioning state across runs; never source it as shell code.
- Keep generated logs and coverage files under `artifacts/`. Each local runner
  invocation must use a distinct directory under `artifacts/runs/`; never mix
  new request diagnostics into an earlier run.
- Keep checked-in OpenAPI snapshots under `schemas/`.
- Keep deferred admin/global operations explicit in
  `schemas/deferred-admin-operations.yaml` once the coverage phase is active.
- Make JSON artifacts stable and readable with indentation where they are meant
  for review.
- Preserve OpenAPI operation ids exactly. Do not rename them in test markers.

## Error Handling

- Return documented exit codes from tools instead of raising tracebacks for
  expected user errors.
- Convert validation, connection, authentication, and configuration failures into clear
  messages naming the environment file, missing variable, path, operation id,
  marker, or command involved.
- Treat missing authentication prerequisites as an expected stop condition for
  live tests, not as a harness bug.
- Do not guess through product, architecture, schema, API, or workflow
  decisions not present in durable documentation or the current user request.

## Documentation Discipline

Update documentation in the same change as behavior, configuration, CLI,
tooling, marker conventions, environment schema, or workflow changes.

- When creating an implementation or execution plan intended for AI Session
  Handler, follow the
  [AI Session Handler plan format](../ai-session-handler/docs/plan-format.md),
  use its canonical template, replace every placeholder, and retain the
  numbered `## Phase N: Title` headings.

  Run a specific plan through the workspace wrapper with:

  ```bash
  ai-session-handler run \
    --plan /workspace/REPOSITORY/docs/plans/PLAN.md \
    --max-phases 999 \
    --quiet \
    --agent-cmd "/workspace/ai-session-handler/.venv/bin/ai-session-handler-codex-high --model MODEL"
  ```

  Omit `--model MODEL` from the quoted agent command to use the wrapper's
  configured or default model.
- Update `README.md` when setup, commands, naming, usage, or examples change.
- Update `docs/` when architecture, OpenAPI coverage, artifact schema, marker
  grammar, exit codes, CI workflow, or operational behavior changes.
- Update this `AGENTS.md` when agent instructions, quality gates, tooling, or
  repository workflow changes.
- Do not leave documentation updates as follow-up work.
