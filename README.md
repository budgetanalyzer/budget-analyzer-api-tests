# Budget Analyzer API Tests

Standalone black-box API tests for the Budget Analyzer public gateway API.

This repository is being bootstrapped from the orchestration plan in
`../orchestration/docs/plans/openapi-black-box-api-test-repository-plan.md`.

## Bootstrap

```bash
python -m pip install -e ".[dev]"
python -m playwright install chromium
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python -m pytest --env local tests/api_docs
```

Environment files under `environments/` are declarative and non-secret. Auth0
Management API inputs and session cookies are read from the environment
variables named by the selected YAML.

Validate an environment file:

```bash
.venv/bin/python tools/validate-environment.py --env local
```

Run pytest against an environment:

```bash
.venv/bin/python -m pytest --env local
```

Check OpenAPI marker coverage against the checked-in snapshot:

```bash
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
```

Export the coverage artifact, including deferred admin operation status:

```bash
.venv/bin/python tools/export-openapi-coverage.py --env local
```

Fail a staging-style gate if marker-only placeholders reappear:

```bash
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
```

Refresh `schemas/openapi.json` from a live environment only when intentionally
updating the checked-in contract snapshot:

```bash
.venv/bin/python tools/refresh-openapi.py --env local
```

Run the production read-only smoke selection only with a pre-provisioned
read-only `BA_SESSION` value:

```bash
BA_SESSION=... .venv/bin/python -m pytest --env production -m "readonly and production_safe"
```

Run local quality gates:

```bash
.venv/bin/python -m ruff format .
.venv/bin/python -m ruff check . --fix
.venv/bin/python -m mypy src tests
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
```
