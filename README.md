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

Environment files under `environments/` are declarative and non-secret. Session
cookies and pre-provisioned browser user credentials are read from the
environment variables named by the selected YAML.

Validate an environment file:

```bash
.venv/bin/python tools/validate-environment.py --env local
```

All supported origins must use HTTPS. TLS verification is mandatory and uses
the runner's normal system/Python trust bundle; environment files do not expose
a verification switch.

Before an exact-local live run, use the typed prerequisite/bootstrap command:

```bash
.venv/bin/python tools/check-live-prerequisites.py \
  --env local --scope public --require-local-target
```

For exactly `--env local` resolving to `environment_type: local` and
`https://app.budgetanalyzer.localhost`, this command invokes the workspace-owned
`ensure-budget-analyzer-local-ca-trust` helper when it is available. It never
does so for staging, production, environment aliases, or another origin. A
host-native runner without the helper continues with normal verified HTTPS.
If the host-published CA is missing or invalid, run orchestration `./setup.sh`
on the host workstation and retry; do not generate certificates in the agent
container or bypass verification.

Choose the prerequisite scope that matches the run:

```bash
.venv/bin/python tools/check-live-prerequisites.py --env local --scope public
.venv/bin/python tools/check-live-prerequisites.py --env local --scope authenticated
.venv/bin/python tools/check-live-prerequisites.py --env local --scope authorization
```

`public` checks verified ingress, the unauthenticated ext_authz edge, and the
public OpenAPI document. `authenticated` also checks every configured input
needed by the primary acquisition path. `authorization` additionally requires
a usable secondary path. The command reports missing environment-variable
names together without printing their values.

The prerequisite CLI has stable exit codes:

| Code | Category |
| ---: | --- |
| 0 | ready |
| 2 | invalid configuration |
| 3 | required-local target mismatch |
| 4 | local CA publication or trust bootstrap |
| 5 | DNS resolution |
| 6 | connection refusal or timeout |
| 7 | TLS certificate validation |
| 8 | ingress/backend readiness |
| 9 | unexpected unauthenticated gateway behavior |
| 10 | authentication or authorization inputs |

Run pytest against an environment:

```bash
.venv/bin/python -m pytest --env local
```

Pytest automatically runs the highest prerequisite scope required by selected
network-capable fixtures once after collection and before fixture setup. It
does not preflight `--collect-only`, offline harness tests, or snapshot-only
coverage tools. Use `--require-local-target` for agent-run exact-local
acceptance; the option constrains the resolved target and does not change TLS
behavior.

Use supplied `BA_SESSION` cookies without browser startup:

```bash
BA_PRIMARY_SESSION=... BA_SECONDARY_SESSION=... \
  .venv/bin/python -m pytest --env local --session-mode supplied_sessions
```

Run the optional hosted-login/session smoke when pre-provisioned credentials are
available:

```bash
BA_PRIMARY_TEST_USERNAME=... BA_PRIMARY_TEST_PASSWORD=... \
BA_SECONDARY_TEST_USERNAME=... BA_SECONDARY_TEST_PASSWORD=... \
  .venv/bin/python -m pytest --env local -m auth_smoke
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

Snapshot refresh and `--source live` coverage commands run the public
prerequisite/bootstrap path before fetching the deployed OpenAPI document.

Run the production read-only smoke selection only with a supplied read-only
primary `BA_SESSION` value:

```bash
BA_PRIMARY_SESSION=... \
  .venv/bin/python -m pytest --env production -m "readonly and production_safe"
```

## Data Isolation

The suite runs against stable primary and secondary users and owns only
resources created under the current UTC run id, for example
`ba-api-test-20260605T123456Z-1a2b3c4d`. Generated names, descriptions,
account ids, bank names, and saved-view criteria include that run id so tests
assert against resources from the current run instead of assuming an empty
account.

Endpoint-specific delete, hide, and unhide behavior is allowed when that
operation is under test and the target resource was created by the current run.
There is no generic cleanup registry or janitor in this suite. Persistent local
environments may retain namespaced residue; staging reset or environment
recreation is owned by orchestration.

Run local quality gates:

```bash
.venv/bin/python -m ruff format .
.venv/bin/python -m ruff check . --fix
.venv/bin/python -m mypy src tests
.venv/bin/python -m pytest --env local --collect-only
.venv/bin/python tools/check-openapi-coverage.py --env local --fail-missing --fail-placeholder
```
