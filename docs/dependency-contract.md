# Python Dependency Contract

**Status:** The local manifest, runtime pin, tool pins, lock, frozen bootstrap,
local parity inventory, hosted CI, and dependency-evidence workflow are active.
Renovate automation and repository administrator settings are not active yet.

## Contract layers

`pyproject.toml` is the direct compatibility contract. Each registry
requirement has a known-working inclusive lower bound and a reviewed upper
boundary. These ranges state which versions may be considered during an
intentional update; they do not make an installation reproducible.

`.python-version` selects the uv-managed Python 3.12.15 project runtime exactly
for local and hosted setup. The PEP 621 interval remains `>=3.12,<3.13`
because the package supports only the selected Python minor line. The lock is
universal across supported platforms for that line, so its normalized runtime
constraint is `==3.12.*`.

`uv.lock` is the machine-generated exact graph. It records selected direct and
transitive versions, markers, standard PyPI sources, artifacts, and SHA-256
integrity metadata. Review `pyproject.toml`, `.python-version`, and `uv.lock`
together. Never edit `uv.lock` by hand.

The exact `uv` bootstrap and `pip-audit` scanner versions live once in
`[tool.dependency-contract]`. They stay outside the application graph:
`uv` creates and inspects the graph, while `pip-audit` will scan evidence in a
separate tool environment. Documentation, workflows, and the runner script
refer to these pins only by reference, never by version literal; a unit test
enforces that.

`./run-tests` starts with any Python on the supported 3.12 minor line, installs
the pinned `uv` from standard PyPI into the ignored
`.dependency-tools/uv-<pin>/` environment, and uses that pinned tool to
install and resolve the exact `.python-version` runtime from uv's per-user
managed Python store. It then runs `uv lock --check` before
`uv sync --frozen --all-groups`, passing the resolved managed interpreter to
both operations. Downloads are allowed only during the explicit managed
Python installation; subsequent lock, sync, parity, and inventory operations
disable Python downloads. Inherited package-index and Python-selection
overrides are removed. `.venv` therefore contains the editable first-party
package and the lock-selected build, runtime, and development graph, but not
the bootstrap tool.

The readiness marker is content-addressed by `pyproject.toml`,
`.python-version`, `uv.lock`, the resolved managed interpreter, and the `uv` pin. A
matching marker is reused only when `uv sync --check --frozen --all-groups`
also proves that the environment has no missing, changed, or extra selected
distribution. A stale manifest/lock pair fails before synchronization.

## Hosted CI

`.github/workflows/ci.yml` runs for pushes to `main`, pull requests targeting
`main`, and manual dispatch. It has only `contents: read`, forces JavaScript
actions onto Node 24, and uses the organization-approved first-party
`actions/checkout@v7` and `actions/setup-python@v7` sources. Concurrent runs for
the same ref cancel superseded work.

CI reads the exact `.python-version` with `actions/setup-python` to establish
its bootstrap interpreter. The declared `uv` then installs/resolves the same
exact managed project runtime used locally and performs a frozen all-groups
synchronization. CI then runs `tools/check-dependency-contract.py` without
`--uv`; the tool resolves `.dependency-tools/uv-<pin>/bin/uv` from the
`[tool.dependency-contract]` pin, not a workflow literal, so a `uv` pin update
needs no workflow edit. It continues with
deterministic unit tests, local API collection, production-safe API collection,
Ruff format and lint checks, and strict mypy. Both API commands are
collection-only. CI does not install Playwright browsers, acquire test
identities, read application credentials, or contact a Budget Analyzer target.

## Dependency evidence

`.github/workflows/dependency-evidence.yml` runs at 07:17 UTC each Monday, by
manual dispatch, and on trusted `main` pushes that change runtime, manifest,
lock, scanner, bootstrap, evidence, or workflow inputs. The job refuses to run
from another ref, declares only `contents: read`, does not cancel an in-progress
evidence run, and uses the same Node 24-ready first-party action majors as CI
plus `actions/upload-artifact@v7`.

Run the credential-free implementation locally with the pinned interpreter:

```bash
PYTHONPATH=src .venv/bin/python tools/generate-dependency-evidence.py \
  --output artifacts/dependency-evidence
```

The command keeps `uv`, the frozen project environment, and `pip-audit` in
separate environments. It never installs a browser. The exact `pip-audit` pin
is recreated from standard PyPI for each evidence run. The scanner audits the
hashed frozen export with dependency resolution disabled; frozen environment
parity independently proves that the exported selection is the installed test
environment. Only the editable first-party package, the isolated `uv`
bootstrap, and the isolated scanner environment are excluded from graph
parity.

Successful evidence compares normalized package names and exact versions
across four representations: the current-platform lock selection, the frozen
installed inventory, `pip-audit` dependencies, and CycloneDX components.
Duplicates, missing or extra packages, version mismatches, malformed JSON,
incomplete scans, stale locks, unexpected sources, and unexpected scanner exit
codes fail. Exit code 1 is accepted only when complete valid output contains
one or more findings; findings stay visible but do not fail the workflow.

The workflow uploads exactly one artifact named
`python-dependency-evidence-GIT_SHA`, retained for seven days. Its explicit
successful-run allowlist is:

- `bootstrap.log`, `scanner-install.log`, `diagnostics.json`, and
  `metadata.json`;
- `lock-export.txt`, `lock-selection.json`, `installed-packages.json`, and
  `dependency-tree.json`;
- `playwright-browser-metadata.json`, which records package-owned browser
  revisions and explicitly records that no browser installation occurred;
- `pip-audit.json`, `pip-audit.txt`, `sbom.cdx.json`,
  `scanner-status.json`, and `scanner.stderr.log`; and
- `parity.json` and `completion.json`.

The generator checks that every successful-run file is present, nonempty, and
that no undeclared file exists before it writes completion. The upload uses
the same exact path list under `always()` with `if-no-files-found: error`.
Failed runs may therefore retain only setup diagnostics and files produced
before the original error; upload does not erase the original failure.
Diagnostic command output is redacted before persistence, and no session,
authorization, password, cookie, package-index credential, or application
secret is accepted as evidence input.

## Direct requirement inventory

The lower bounds are versions exercised by the existing successful Python
3.12 environment or selected for the fresh lock. Stable projects stop before
their next major line. Pre-1.0 projects whose minor releases may break callers
stop before the next minor line.

| Requirement | Repository use | Compatibility boundary |
| --- | --- | --- |
| `httpx>=0.28.1,<0.29` | Public gateway client, response assertions, and HTTP test transports | HTTPX is pre-1.0; review the next minor line. |
| `jsonschema[format-nongpl]>=4.26.0,<5` | OpenAPI response validation with `FormatChecker` | Review the next major line; the extra supplies format validators. |
| `playwright>=1.63.0,<2` | Hosted browser signup/login and the coupled Chromium revision | Review the next major line; Playwright owns the browser revision. |
| `pydantic>=2.14.0,<3` | Typed environment configuration and validation | Review the next major line. |
| `PyYAML>=6.0.3,<7` | Environment and deferred-operation YAML parsing | Review the next major line. |
| `pytest>=9.1.1,<10` | Test runner, fixtures, markers, and repository plugin | Review the next major line. |
| `referencing>=0.37.0,<0.38` | OpenAPI `$ref` registry and Draft 2020-12 resources | The library is pre-1.0; review the next minor line. |
| `setuptools>=84.0.0,<85` | PEP 517 build backend for the editable first-party package | Review the next backend major line. It is repeated in the `build` group so no-build-isolation installs use the locked tool. |
| `mypy>=2.4.0,<3` | Strict static type checking of `src` and `tests` | Review the next major line. |
| `ruff>=0.17.0,<0.18` | Formatting, linting, import sorting, and pyupgrade | Ruff is pre-1.0; review the next minor line. |
| `types-PyYAML>=6.0.12.20260906,<7` | Type information for direct `yaml` imports under strict mypy | Keep the stubs on the PyYAML 6 compatibility line. |

`respx` was removed because the repository has no imports or tests using it;
the existing HTTP tests use `httpx.MockTransport`. Runtime packages remain in
`project.dependencies`. Build and development tools use PEP 735
`dependency-groups` and are not published as an application extra.

## Intentional lock refresh

Read the tool pin with Python's native TOML parser and install that exact `uv`
in an isolated temporary environment:

```bash
UV_VERSION=$(python3.12 -c \
  'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))["tool"]["dependency-contract"]["uv"])')
python3.12 -m venv /tmp/budget-analyzer-api-tests-dependency-tools
/tmp/budget-analyzer-api-tests-dependency-tools/bin/python -m pip install \
  "uv==$UV_VERSION"
UV_BIN=/tmp/budget-analyzer-api-tests-dependency-tools/bin/uv
```

After intentionally changing reviewed bounds or tool/runtime pins, regenerate
the complete graph from standard PyPI and validate it:

```bash
"$UV_BIN" lock --upgrade --python "$(cat .python-version)" --no-sources
PYTHONPATH=src .venv/bin/python tools/check-dependency-contract.py --uv "$UV_BIN"
"$UV_BIN" export --frozen --all-groups --no-emit-project --no-annotate \
  --no-header --format requirements.txt --python "$(cat .python-version)"
```

For a narrow update, use `uv lock --upgrade-package NAME` with the same
Python and source flags. Review manifest rationale, lock source and marker
changes, exact versions, artifacts, and hashes before accepting the refresh.
Normal validation uses `uv lock --check` and `uv export --frozen`; it must not
refresh the lock as a side effect.

Full local runs write `dependency-inventory.json` in their unique ignored
`artifacts/runs/` directory. The JSON records the exact runtime and `uv`
versions, the lock SHA-256 and universal package set, the editable first-party
project separately, the selected installed third-party set, and the successful
frozen parity check. Inventory generation fails instead of publishing partial
evidence when the lock is stale, the environment differs, the project is not
the repository editable, or an installed package/version is absent from the
lock.

The first-party editable package is the only non-registry lock source. All
third-party packages must resolve from `https://pypi.org/simple`; VCS, URL,
path, alternate-index, and additional editable sources are rejected by the
repository checker. The checker also rejects unbounded direct requirements,
missing build/development groups, inconsistent Python constraints, stale
locks, and exported requirements without exact versions and SHA-256 hashes.
