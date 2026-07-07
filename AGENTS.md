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

Keep changes scoped to the requested plan phase or user request. The source
implementation plan is
`../orchestration/docs/plans/openapi-black-box-api-test-repository-plan.md`.

## Core Product Constraints

- Keep this a black-box public API test suite. Do not add direct database,
  Redis, RabbitMQ, Kubernetes, service-DNS, or service-internal setup paths.
- Compose API requests as `{origin}{api_base_path}{openapi path}` through
  `GatewayClient`.
- Treat the OpenAPI document as the executable coverage manifest.
- Keep environment YAML declarative and non-secret. Read secrets only from
  environment variables or CI secret stores.
- Do not write Auth0 Management API tokens, generated passwords, session
  cookies, authorization headers, or other credentials to artifacts.
- Use `browser_auth0` as the primary local and staging auth mode. `env_cookie`
  is allowed for local debugging and for production read-only smoke runs with a
  supplied, pre-provisioned `BA_SESSION`.
- Production defaults must remain read-only: `allow_mutation: false` and
  `allow_destructive: false`.
- Do not broaden production permissions to reach coverage targets.
- Keep admin/global operations deferred until a separate admin identity,
  cleanup, and global-state safety plan exists.
- Do not add generic cleanup callbacks or janitors in the initial suite.
  Per-run generated users are the boundary for user-owned test data.

## Implemented Workflow

- Install the local development environment with:

  ```bash
  python -m pip install -e ".[dev]"
  python -m playwright install chromium
  ```

- Validate environment configuration with:

  ```bash
  .venv/bin/python tools/validate-environment.py --env local
  ```

- Run local collection without a live target with:

  ```bash
  .venv/bin/python -m pytest --env local --collect-only
  ```

- Check OpenAPI marker coverage against the checked-in snapshot with:

  ```bash
  .venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
  ```

- Export the OpenAPI coverage artifact with:

  ```bash
  .venv/bin/python tools/export-openapi-coverage.py --env local
  ```

- Live API tests require the selected environment, Auth0 prerequisites, browser
  login, and network access to be available.
- Production smoke execution requires a supplied read-only `BA_SESSION` and
  must use:

  ```bash
  pytest --env production -m "readonly and production_safe"
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
  environment config, identities, run state, operation coverage, and process or
  request results.
- Use literals or enums for constrained status values, auth modes, environment
  types, cleanup modes, and coverage states.
- Do not silence mypy with broad ignore comments. A narrow ignore must include
  the error code and a short reason.

## Formatting And Linting

Use Ruff as the single formatting, linting, pyupgrade, and import-sorting path.
Do not add Black, isort, Flake8, or autopep8 unless the user explicitly asks
for that tool split.

Expected commands in this workspace use the repository-local virtualenv. If
`.venv/` is missing, create or install the dev environment first rather than
falling back to global tooling.

Current transitional commands:

```bash
.venv/bin/python -m ruff format .
.venv/bin/python -m ruff check . --fix
.venv/bin/python -m mypy src tests
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
```

After the `src/` migration, the mypy command should be:

```bash
.venv/bin/python -m mypy src tests
```

Ruff should use `line-length = 100`, `target-version = "py312"`,
`src = ["src", "tests"]`, and lint selections including `E`, `F`, `I`, `UP`,
`B`, `SIM`, and `RUF`.

## Testing Standards

Use pytest for tests.

- Prefer `tmp_path`, `monkeypatch`, `capsys`, `httpx.MockTransport`, and
  `respx` for harness unit tests.
- Do not call real Auth0, real providers, or live Budget Analyzer targets from
  unit tests.
- Keep tests deterministic. Avoid sleeps except where timeout behavior is under
  test, and keep those durations short.
- Use OpenAPI markers consistently:

  ```python
  @pytest.mark.openapi("operationId")
  ```

- Use `placeholder` only while a phase is under construction. Placeholder
  coverage is not an acceptable staging-gate completion state.
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
  timeout, cookies, and request logging stay centralized.
- Use `raw_request` only for `/api-docs`, auth edge checks, and other explicitly
  public non-API-prefix routes.
- Never seed sessions in Redis, exchange Auth0 tokens directly for API access,
  or bypass the Session Gateway.
- Stream or record enough request details for diagnostics, but redact secrets
  before writing artifacts.
- Keep mutating tests guarded by environment policy before any mutating request
  is made.

## State, Files, And Schemas

- Keep environment files under `environments/` non-secret.
- Keep generated logs and coverage files under `artifacts/`.
- Keep checked-in OpenAPI snapshots under `schemas/`.
- Keep deferred admin/global operations explicit in
  `schemas/deferred-admin-operations.yaml` once the coverage phase is active.
- Make JSON artifacts stable and readable with indentation where they are meant
  for review.
- Preserve OpenAPI operation ids exactly. Do not rename them in test markers.

## Error Handling

- Return documented exit codes from tools instead of raising tracebacks for
  expected user errors.
- Convert validation, prerequisite, and configuration failures into clear
  messages naming the environment file, missing variable, path, operation id,
  marker, or command involved.
- Treat missing Auth0 prerequisites as an expected stop condition for live
  tests, not as a harness bug.
- Do not guess through product, architecture, schema, API, or workflow
  decisions not present in durable documentation or the current user request.

## Documentation Discipline

Update documentation in the same change as behavior, configuration, CLI,
tooling, marker conventions, environment schema, or workflow changes.

- When creating plans, break them into `## Phase ...` sections. Each phase
  should be scoped so it can be completed in a single AI session.
- Update `README.md` when setup, commands, naming, usage, or examples change.
- Update `docs/` when architecture, OpenAPI coverage, artifact schema, marker
  grammar, exit codes, CI workflow, or operational behavior changes.
- Update this `AGENTS.md` when agent instructions, quality gates, tooling, or
  repository workflow changes.
- Do not leave documentation updates as follow-up work.
