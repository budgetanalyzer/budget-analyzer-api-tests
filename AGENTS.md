# Budget Analyzer API Tests

## Repository Position

**Archetype:** black-box API test harness

**Scope:** Budget Analyzer system API surface inventory and public gateway behavior

**Role:** inventories every inbound API surface and validates supported behavior
through the public browser-facing request path

### Boundaries

- Read sibling repositories when their public contracts or orchestration
  context are relevant to the requested test work.
- Keep writes in this repository unless the user explicitly changes the task
  scope.
- Do not change sibling service implementation or deployment configuration
  from this repository context.

## Code Exploration

Never use Agent or subagent tools for code exploration. Use direct repository
searches and targeted file reads instead:

```bash
rg --files
rg -n "pattern" .
find . -maxdepth 2 -type f | sort
```

## Repository Purpose

This repository contains standalone contract inventory and black-box tests for
Budget Analyzer API surfaces. It tests supported deployed behavior through the
same public lane used by the browser:

```text
test runner -> HTTPS ingress -> ext_authz -> NGINX -> services
```

The suite must stay separate from service implementation repositories and must
not depend on service internals. Inventory inaccessible mesh, operational, and
development-only surfaces explicitly; never ignore them or bypass architecture
boundaries to turn them into executable tests.

## Session Initialization

At the start of work:

1. Read this `AGENTS.md`.
2. Inspect the repository structure before editing.
3. Read the nearest relevant modules, tests, environment files, and
   source-of-truth documents before changing them.

Keep changes scoped to the current user request or its selected plan phase.
Consult the relevant source-of-truth files for the behavior being changed; do
not infer the current task from plans that merely exist in `docs/plans/`.

## Prerequisites First

Before implementing a plan or feature, identify its required environment,
trust, deployment, credentials, and tools in the relevant documentation. If a
required prerequisite is missing, stop and report it. Do not bypass, weaken, or
replace missing prerequisites with test-harness workarounds.

## Core Product Constraints

- Keep this a black-box public API test suite. Do not add direct database,
  Redis, RabbitMQ, Kubernetes, service-DNS, or service-internal setup paths.
- Compose API requests as `{origin}{api_base_path}{openapi path}` through
  `GatewayClient`.
- Treat Istio ingress as the sole owner of the four public per-client rate
  policies. Share each applicable pacer across all `GatewayClient` instances and
  same-origin browser requests in one process; do not partition pacing by
  account or cookie. Do not classify bare `/login` or identity-provider origins
  as application policy traffic. Read
  `../orchestration/docs/architecture/security-architecture.md` and
  `../orchestration/kubernetes/istio/ingress-rate-limit.yaml` before changing
  pacing constants or path matching.
- Treat each namespaced OpenAPI document and the Session Gateway inbound-route
  inventory as executable coverage manifests.
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
- Keep mesh-internal Session Gateway behavior classified as untestable by this
  public harness. Do not add pod-specific requests, forge or delegate workload
  identity, or imply that a trusted service-to-service runner is planned. An
  optional future admin deactivation scenario may prove end-to-end revocation,
  but it must not reclassify the internal hop.
- Do not add generic cleanup callbacks or janitors. Use the pytest session's
  run id and resources created by the current test as the user-data boundary.

## Implemented Workflow

- Bootstrap and run the full local suite with `./run-tests`. It uses a Python
  on the supported minor line to install the declared `uv` pin separately,
  lets pinned `uv` install/cache the exact `.python-version` runtime, frozen
  synchronizes and verifies `.venv` with that managed interpreter against
  `uv.lock`, installs Chromium when needed, fixes the target to the declared
  local environment, lazily provisions browser accounts, checks snapshot
  coverage and live contract drift, and writes an isolated ignored run report.
  Pass ordinary pytest selectors through this command, and treat any run with
  selectors as partial validation.

- Hosted CI performs frozen dependency setup, deterministic tests, both offline
  API collection gates, Ruff, and strict mypy without installing browsers or
  contacting a deployment. The scheduled/trusted-main dependency-evidence
  workflow creates one exact seven-day allowlisted artifact. Reproduce it with
  the exact pinned interpreter using
  `PYTHONPATH=src .venv/bin/python tools/generate-dependency-evidence.py`.

- Before changing Python declarations, the runtime or isolated tool pins,
  `uv.lock`, dependency workflows, evidence behavior, or `renovate.json`, read
  `docs/dependency-automation.md` and `docs/dependency-contract.md`. Discover
  current owned declarations with:

  ```bash
  rg -n '(^requires =|dependencies =|include-group|renovate:|uses:)' \
    pyproject.toml .github/workflows renovate.json
  ```

  For Renovate changes, run the strict validation and credential-free
  extraction commands documented there with the orchestration-owned Node and
  Renovate pins. Report anonymous lookup limitations; do not treat them as
  successful hosted acceptance.

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

  This command writes `artifacts/openapi-coverage.json` and
  `artifacts/session-gateway-coverage.json`. It fails for unrecognized or
  malformed namespaced markers, missing or placeholder-only non-deferred
  operation coverage, executable Session Gateway cases without markers, stale
  deferred entries, incomplete route/case classifications, and Session Gateway
  inventory/snapshot conflicts. The Session Gateway artifact and CLI report
  operation and scenario classifications separately, including counts and ids.
  Keep snapshot refresh separate and intentional. The normal no-argument
  `./run-tests` workflow performs coverage and unified live structural drift
  checks itself; do not require operators to invoke either tool first.

  The reviewed Session Gateway baseline is 3 covered filtered OpenAPI
  operations and 34 scenarios: 14 covered, 11 deferred, and 9 untestable across
  14 inbound routes. Treat `schemas/session-gateway-inbound-inventory.yaml` and
  the generated artifact as the source of truth; update this summary in the
  same change when classifications change. Say “all known inbound routes
  inventoried” and “all currently executable public cases covered.” Never claim
  full Session Gateway behavior coverage while deferred or untestable cases
  remain.

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
PYTHONPATH=src .venv/bin/python tools/check-dependency-contract.py \
  --uv .dependency-tools/uv-0.13.0/bin/uv
.venv/bin/python -m pytest tests/unit --env local
.venv/bin/python -m pytest tests/api --env local --collect-only
.venv/bin/python -m pytest tests/api --env production -m "readonly and production_safe" --collect-only
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
actionlint .github/workflows/*.yml
shellcheck run-tests
./run-tests
```

Ruff should use `line-length = 100`, `target-version = "py312"`,
`src = ["src", "tests"]`, and lint selections including `E`, `F`, `I`, `UP`,
`B`, `SIM`, and `RUF`.

## Testing Standards

Use pytest for tests.

- Keep deterministic tests under `tests/unit/` and deployed public HTTP tests
  under `tests/api/`. Keep `pytester` enabled only in the unit subtree.
- Prefer `tmp_path`, `monkeypatch`, `capsys`, and `httpx.MockTransport` for
  harness unit tests.
- Do not call real identity providers or live Budget Analyzer targets from unit
  tests.
- Keep tests deterministic. Do not add sleeps to individual API tests; public
  gateway rate compliance belongs in the shared `GatewayClient` pacer. Use
  injected fake timing to unit-test pacing. Keep sleeps for timeout-specific
  unit tests short.
- Use OpenAPI markers consistently:

  ```python
  @pytest.mark.openapi("operationId")
  @pytest.mark.openapi("getCurrentUser", contract="session_gateway")
  @pytest.mark.session_gateway_case("current-user-authenticated")
  ```

  Keep the one-argument form as the unified contract default. Always qualify
  Session Gateway operations so equal operation ids cannot merge coverage.
  Use `session_gateway_case` only for exact `covered` ids in the inbound
  inventory; use `gateway_routing` for public route/method boundaries that are
  not OpenAPI operations.

- Keep browser/session smoke outcomes identifiable. Use `auth_smoke` for the
  primary account and `secondary_auth_smoke` for the independently lazy
  secondary account. They run in the full local suite and remain directly
  selectable with ordinary pytest marker expressions.
- Use `placeholder` only while a phase is under construction. Placeholder
  coverage is not an acceptable local regression baseline.
- Mark mutating tests with `mutation`, destructive tests with `destructive`,
  read-only tests with `readonly`, missing or invalid identity tests with
  `authentication`, authorization or ownership-isolation tests with
  `authorization`, and production-safe tests with `production_safe`.
- Keep all documented `403` responses in
  `schemas/deferred-forbidden-responses.yaml` until a separate authorization
  plan defines a safe restricted principal. Do not reinterpret privacy-safe
  cross-user `404` or domain `422` responses as `403` coverage.
- Production execution should select only explicitly production-safe read-only
  tests.
- Mutation and destructive markers must be blocked by environment policy before
  request fixtures or network-capable clients are set up.
- For pytest configuration, prefer strict config, strict markers, declared test
  paths, and importlib import mode.

## API Execution Safety

- Use `GatewayClient` for API requests so base origin, API prefix, TLS policy,
  timeout, cookies, request logging, and public-gateway pacing stay centralized.
- Preserve the four independent conservative paces for login initiation, login
  completion, session operations, and API requests as deliberate compliance
  with public ingress. Do not retry HTTP 429 responses, add test-level sleeps,
  bypass ingress, forge client identity headers, or add environment-specific
  exceptions. Treat any `x-local-rate-limit-policy` rejection as a pacing
  regression or an external concurrent-traffic prerequisite failure. Remember
  that independent harness processes and external callers sharing the same
  public IP are not coordinated by the in-process pacers.
- Preserve HTTPS certificate verification for live tests in every environment,
  including local Tilt targets. Do not add insecure runner flags, environment
  switches, `verify=False`, Playwright `ignore_https_errors`, or equivalent
  bypasses for agent containers.
- Treat HTTPS trust and browser system dependencies as runtime prerequisites.
  The declared uv-managed Python is the only runner prerequisite this
  repository installs. Do not install trust anchors, invoke external trust
  helpers, generate or rotate certificates, or modify any other runtime
  prerequisite from this repository. Report a request or login failure if a
  prerequisite is absent.
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
- Keep the exhaustive Session Gateway route/case inventory under `schemas/`.
  Classify inaccessible behavior as deferred with a concrete prerequisite or
  untestable with an enforced architectural boundary; do not silently omit it.
- Keep deferred admin/global operations explicit in
  `schemas/deferred-admin-operations.yaml` once the coverage phase is active.
- Classify every documented `403` by exact operation id, method, path, and
  deferral reason in `schemas/deferred-forbidden-responses.yaml`. Keep admin
  operations deferred regardless of HTTP method until an admin identity,
  cleanup, and global-state safety model is approved.
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
  repo_root=$(git rev-parse --show-toplevel)
  workspace_parent=$(dirname "$repo_root")
  ai-session-handler run \
    --plan "$repo_root/docs/plans/PLAN.md" \
    --max-phases 999 \
    --quiet \
    --agent-cmd "$workspace_parent/ai-session-handler/.venv/bin/ai-session-handler-codex-high --model MODEL"
  ```

  Omit `--model MODEL` from the quoted agent command to use the wrapper's
  configured or default model.
- Update `README.md` when setup, commands, naming, usage, or examples change.
- Update `docs/` when architecture, OpenAPI coverage, artifact schema, marker
  grammar, exit codes, CI workflow, or operational behavior changes.
- Update this `AGENTS.md` when agent instructions, quality gates, tooling, or
  repository workflow changes. Before editing it, read
  `../orchestration/docs/agents-md-checkstyle.md` and apply that authoring
  standard.
- Do not leave documentation updates as follow-up work.

## Path Portability

Never hardcode absolute workspace or checkout paths such as `/workspace`. Use
repository-relative paths, sibling-relative paths, or roots derived at runtime
with commands such as `git rev-parse --show-toplevel`.

## No Git Write Operations

Never run Git write commands such as `commit`, `push`, `checkout`, `reset`, or
branch manipulation unless the user explicitly requests them. Read-only
inspection commands such as `git status`, `git diff`, and `git log` are
allowed.
