# Budget Analyzer API Tests

Standalone black-box API tests for the Budget Analyzer public gateway API.

This repository is being bootstrapped from the orchestration plan in
`../orchestration/docs/plans/openapi-black-box-api-test-repository-plan.md`.

## Bootstrap

```bash
python -m pip install -e ".[dev]"
python -m playwright install chromium
pytest --env local --collect-only
pytest --env local tests/api_docs
```

Environment files under `environments/` are declarative and non-secret. Auth0
Management API inputs and session cookies are read from the environment
variables named by the selected YAML.

Validate an environment file:

```bash
python tools/validate-environment.py --env local
```

Run pytest against an environment:

```bash
pytest --env local
```

Run local type checks:

```bash
.venv/bin/python -m mypy src tests
```
