# Plan: Python Baseline Remediation

Date: 2026-07-05
Status: Review complete, implementation pending

Reviewed inputs:

- `../orchestration/docs/plans/openapi-black-box-api-test-repository-plan.md`
- `pyproject.toml`
- `pytest.ini`
- `.gitignore`
- `api_tests/`
- `tests/`
- `tools/`
- `environments/`

## Goal

Align `budget-analyzer-api-tests` with the Python baseline used across the
workspace while preserving the black-box API test constraints from the OpenAPI
test repository plan.

The goal is consistency in Python project shape, tooling, typing, tests,
artifact hygiene, and safety defaults. This plan does not change endpoint
coverage scope.

## Current Validation Results

Commands run from this repository:

```bash
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy api_tests tests
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python -m pytest --env local
.venv/bin/python tools/validate-environment.py --env local
```

Observed results:

- Ruff format check fails: `api_tests/auth0.py`,
  `api_tests/browser_login.py`, `api_tests/config.py`, and
  `tests/auth/test_phase1_auth_helpers.py` would be reformatted.
- Ruff lint passes with the current, minimal Ruff configuration.
- mypy strict fails with two errors:
  - `tests/conftest.py`: generator fixture return type should be `Generator`
    or a supertype.
  - `tests/auth/test_phase1_auth_helpers.py`: `BrowserContext` is imported
    through `api_tests.browser_login`, but that module does not explicitly
    export it.
- `pytest --env local --collect-only` passes and collects 15 tests.
- `pytest --env local` produces 14 passes and one expected live-test setup
  error when Auth0 prerequisites are absent:
  `AUTH0_MGMT_DOMAIN` is missing.
- `tools/validate-environment.py --env local` succeeds.

## Conformance Findings

1. The implementation uses a root-level `api_tests/` package. The workspace
   Python baseline prefers `src/` layout to avoid accidental imports from the
   repository root.
2. Tool configuration is split between `pyproject.toml` and `pytest.ini`.
   The baseline prefers packaging, Ruff, mypy, and pytest configuration in
   `pyproject.toml`.
3. Ruff is underconfigured compared with the baseline. It sets line length and
   target version, but does not set `src` or lint selections such as `E`, `F`,
   `I`, `UP`, `B`, `SIM`, and `RUF`.
4. pytest configuration lacks strict config, strict markers, declared test
   paths, and importlib import mode.
5. mypy is strict, but the checked command should cover tests and eventually
   `src`. Tool scripts are not currently part of the type-checking story.
6. Several tool files are placeholders. `tools/validate-environment.py` works,
   but it inserts the repository root into `sys.path`; this should disappear
   after a `src/` layout and editable install are in place.
7. The `.gitignore` is broad and appears copied from a Java, Spring Boot, and
   React template. It includes the needed Python generated artifacts, but it is
   noisier than this repository needs.
8. The production environment currently uses `browser_auth0`. The repository
   plan says production should not create per-run Auth0 users; production needs
   an explicit read-only smoke-session strategy before production tests are
   operational.
9. The repository had no `AGENTS.md`. That instruction file has now been added
   so future work has local Python, testing, and black-box safety rules.

## Remediation Phases

### Phase 1: Make Current Gates Green

Goal: fix formatting and strict typing failures without changing behavior.

Steps:

1. Run Ruff formatting on the repository.
2. Fix the `gateway_client` fixture return annotation in `tests/conftest.py`
   using `Generator[GatewayClient]` or an equivalent precise type.
3. Fix the `BrowserContext` typing issue by importing it from
   `playwright.sync_api` in the test or by explicitly exporting it from a
   dedicated test helper.
4. Re-run:

   ```bash
   .venv/bin/python -m ruff format --check .
   .venv/bin/python -m ruff check .
   .venv/bin/python -m mypy api_tests tests
   .venv/bin/python -m pytest --env local --collect-only
   ```

Acceptance checks:

- Ruff format and lint pass.
- mypy strict passes for implementation and tests.
- pytest collection still succeeds without live Auth0 prerequisites.

### Phase 2: Move To `src/` Layout

Goal: align package layout with the workspace Python baseline.

Steps:

1. Move `api_tests/` to `src/api_tests/`.
2. Update `pyproject.toml`:
   - set `[tool.setuptools.packages.find] where = ["src"]`
   - set `[tool.ruff] src = ["src", "tests"]`
   - keep `packages = ["api_tests"]` for mypy
3. Update imports only where the move exposes test assumptions.
4. Update tool scripts so they rely on the installed package instead of
   manually adding the repository root to `sys.path`.
5. Update README and AGENTS commands from `mypy api_tests tests` to
   `mypy src tests`.

Acceptance checks:

- Editable install imports `api_tests` from `src/api_tests`.
- Running pytest with importlib import mode still imports the installed package,
  not a package from the repository root.
- `mypy src tests` passes.

### Phase 3: Consolidate Tooling Configuration

Goal: make this repo's tool configuration match the standard workspace shape.

Steps:

1. Move pytest settings from `pytest.ini` into
   `[tool.pytest.ini_options]` in `pyproject.toml`.
2. Add:
   - `testpaths = ["tests"]`
   - `addopts = "--strict-config --strict-markers --import-mode=importlib"`
3. Add Ruff lint selections:

   ```toml
   [tool.ruff.lint]
   select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]
   ```

4. Delete `pytest.ini` after the pyproject configuration is verified.
5. Decide whether tool scripts should become package entry points or stay as
   checked script files with typed `main()` functions.

Acceptance checks:

- pytest reports `configfile: pyproject.toml`.
- Strict marker checking still recognizes all custom markers.
- Ruff lint remains clean under the broader lint selection.

### Phase 4: Tighten Types And Tool Entrypoints

Goal: reduce loose boundary typing before the OpenAPI coverage harness grows.

Steps:

1. Replace broad `Any` usage where practical with typed boundary models,
   protocols, or local aliases.
2. Keep unavoidable external-boundary `Any` close to decoded YAML, decoded JSON,
   OpenAPI documents, httpx hooks, pytest hooks, and Playwright objects.
3. Implement each non-placeholder tool with
   `main(argv: Sequence[str] | None = None) -> int`.
4. Add unit tests for tool argument parsing and failure messages.
5. Add mypy coverage for tools if they remain script files, or move tool logic
   into package modules and type-check those modules through `src`.

Acceptance checks:

- Tool behavior is covered by tests that do not require live network access.
- Expected user errors return clear messages and non-zero exit codes instead
  of tracebacks.
- Strict mypy remains clean.

### Phase 5: Production Safety Alignment

Goal: make production behavior match the plan's read-only safety model.

Steps:

1. Define the production authentication strategy before enabling production
   runs:
   - stable pre-provisioned smoke identity, or
   - supplied `BA_SESSION` with explicit production-safe handling.
2. Adjust `auth.mode` validation so production cannot accidentally create
   per-run users.
3. Add guard fixtures for `mutation` and `destructive` markers before mutating
   endpoint tests are implemented.
4. Add tests proving mutation and destructive tests skip before making network
   requests when disabled.
5. Document the production command:

   ```bash
   pytest --env production -m "readonly and production_safe"
   ```

Acceptance checks:

- Production defaults cannot create users or mutate data.
- Mutating tests are blocked by policy before request execution.
- Read-only production smoke behavior is explicit in README and AGENTS.

### Phase 6: Repository Hygiene

Goal: keep generated artifacts and instructions clean.

Steps:

1. Replace the broad Java/React `.gitignore` with a repository-specific ignore
   file covering Python, pytest, Ruff, mypy, Playwright artifacts, local
   virtualenvs, generated test artifacts, and secrets.
2. Confirm generated caches and egg-info files are not tracked.
3. Keep `artifacts/.gitkeep`, `fixtures/statements/.gitkeep`,
   `schemas/.gitkeep`, and environment skeleton files tracked.
4. Update README to show the same local quality gates as AGENTS.

Acceptance checks:

- `git status --ignored --short` shows generated Python and test artifacts are
  ignored.
- The tracked file list contains only source, tests, docs, config, schemas,
  fixtures, and intentional placeholders.

### Phase 7: Resume OpenAPI Coverage Work

Goal: continue the original repository plan after Python baseline alignment.

Steps:

1. Implement the Phase 2 OpenAPI coverage harness from the orchestration plan.
2. Add `schemas/openapi.json` and
   `schemas/deferred-admin-operations.yaml`.
3. Add operation inventory and marker coverage tests.
4. Replace marker-only placeholders with real tests according to later phases.

Acceptance checks:

- Coverage fails for new non-deferred operations without markers.
- Deferred admin/global operations are explicit and reported separately.
- Placeholder coverage is visible and can be failed in staging gates.

## Recommended Immediate Order

1. Phase 1, because it is low risk and makes the current repo healthy.
2. Phase 2 and Phase 3 together, because layout and tool config affect the same
   imports and commands.
3. Phase 5 before any production workflow is introduced.
4. Phase 7 after the Python baseline is stable.
