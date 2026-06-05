# Budget Analyzer API Tests

Standalone black-box API tests for the Budget Analyzer public gateway API.

This repository is being bootstrapped from the orchestration plan in
`../orchestration/docs/plans/openapi-black-box-api-test-repository-plan.md`.

## Bootstrap

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
playwright install
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
