# Dependency Automation

**Status:** Repository preparation is complete, but Mend Renovate and GitHub
dependency settings are not yet activated. Hosted acceptance evidence is still
required.

The central [Budget Analyzer dependency-automation policy](https://github.com/budgetanalyzer/orchestration/blob/main/docs/dependency-automation.md)
owns service selection, scheduling, cost, credentials, administrator settings,
and activation. This document owns the API-tests repository's dependency files,
commands, evidence semantics, Renovate behavior, and failure triage.

## Owned dependency surfaces

- `pyproject.toml` is the direct compatibility contract for runtime, test,
  development, and build requirements. Its `[tool.dependency-contract]` table
  also owns the exact isolated `uv` bootstrap and `pip-audit` scanner pins.
- `.python-version` owns the exact supported Python patch. Keep it within the
  `requires-python` minor interval in `pyproject.toml`.
- `uv.lock` is the generated exact cross-platform graph. Never edit it by hand.
- `.github/workflows/*.yml` owns the GitHub Actions references and the events,
  permissions, and evidence allowlist used by hosted checks.
- `renovate.json` extends exactly the shared orchestration preset and owns only
  repository-specific extraction, lock maintenance, grouping, and review notes.
- `docs/dependency-contract.md` owns direct-dependency rationale, exact
  lock-refresh commands, graph invariants, evidence filenames, and detailed
  evidence interpretation.

Use `rg -n '(^requires =|dependencies =|include-group|renovate:|uses:)' pyproject.toml .github/workflows renovate.json`
to rediscover current declarations; do not maintain a version inventory in this
document.

## Renovate behavior

Renovate uses native managers first:

- PEP 621 extracts the bounded direct requirements and maintains `uv.lock` as
  the associated lock artifact.
- the native Python-version manager extracts `.python-version`;
- GitHub Actions extracts every `uses:` reference; and
- two narrow regex managers extract only the annotated `uv` and `pip-audit`
  declarations that native managers do not model.

No dependency manager is disabled. The current file shapes do not produce a
native-manager overlap. A file-scoped package rule disables updates under the
gitignored `artifacts/` tree without replacing the shared preset's path
exclusions. Generated evidence must be recreated by the repository tools,
never rewritten as a dependency declaration.

Weekly lock-file maintenance runs in the shared Monday window so admissible
transitive updates receive a reviewed pull request even when a direct range does
not change. The repeated `setuptools` build requirement is grouped as one build
contract. Other direct packages are intentionally not grouped because they do
not share a release train. The Playwright package remains the sole owner of its
coupled browser revision; review regenerated browser metadata rather than
introducing another version pin.

The shared preset keeps automerge off, requires dashboard approval for major
updates, limits pull-request concurrency, and labels routine updates. A proposed
change is not acceptable unless the manifest, generated lock, supported runtime,
frozen installation, full deterministic CI, exact graph evidence, and relevant
browser or Action compatibility remain aligned. Never merge a manifest-only
proposal when lock generation failed.

## Local validation and intentional refresh

Normal setup and CI are frozen and must not update `uv.lock`. For a deliberate
dependency update, follow the refresh sequence in `docs/dependency-contract.md`,
review all source/version/hash changes, then run:

```bash
PYTHONPATH=src .venv/bin/python tools/check-dependency-contract.py \
  --uv .dependency-tools/uv-0.13.0/bin/uv
.dependency-tools/uv-0.13.0/bin/uv lock --check
.venv/bin/python -m pytest tests/unit --env local
.venv/bin/python -m pytest tests/api --env local --collect-only
.venv/bin/python -m pytest tests/api --env production \
  -m "readonly and production_safe" --collect-only
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src tests
actionlint .github/workflows/*.yml
shellcheck run-tests
PYTHONPATH=src .venv/bin/python tools/generate-dependency-evidence.py
```

Validate `renovate.json` with the exact Renovate release and Node major declared
by orchestration's `.github/workflows/dependency-automation-config.yml`. From a
checkout where the prepared files are Git-tracked, set `RENOVATE_VERSION` to
that workflow's current pin and run:

```bash
npx --yes --package="renovate@${RENOVATE_VERSION}" \
  renovate-config-validator --strict --no-global renovate.json
LOG_LEVEL=debug npx --yes --package="renovate@${RENOVATE_VERSION}" renovate \
  --platform=local --dry-run=full --onboarding=false \
  --require-config=required --repository-cache=disabled
```

Do not pass a repository argument with `--platform=local`; the engine inspects
the current checkout. Review extraction by manager, file, and package identity
rather than relying on a historical count. Confirm that the effective config
resolves the shared preset, weekly lock maintenance, and the generated-evidence
exclusion. Repeated identities are expected only where the repository really
declares them: `setuptools` in build-system and build-group contracts, Python in
the compatibility and exact-runtime contracts, and shared Actions in both
workflows. The Actions manager also reports each `runs-on` runner label as a
non-versioned runner identity, and the Renovate-config manager reports the
default-branch shared preset without a version; neither is a second package
declaration. Anonymous GitHub or registry lookups can be rate-limited or
unavailable; record unresolved lookups for hosted acceptance instead of
treating a zero exit status as proof that every lookup succeeded.

The credential-free pre-activation run resolved the shared preset and PyPI
package identities, but anonymous GitHub Action lookups hit the public API rate
limit and exact Python runtime metadata could not complete its unauthenticated
registry lookup. Those lookup results remain unproven until the retained hosted
Mend run succeeds with normal service access; the local zero exit status is
extraction evidence only.

The dependency-evidence command recreates the bounded allowlist under
`artifacts/dependency-evidence/`. A successful result proves exact normalized
name-and-version parity among the current-platform lock selection, frozen
installation, dependency tree, CycloneDX SBOM, and `pip-audit` records. Findings
are non-gating only when the scan and parity evidence are complete. Missing,
extra, malformed, duplicate, or mismatched records remain operational failures.
The scheduled/trusted-main workflow retains one allowlisted artifact for seven
days and does not install a browser or contact a Budget Analyzer deployment.

## Failure triage

- For extraction omissions or duplicates, compare each manager/file identity in
  dry-run logs. Narrow a regex to one owned declaration; do not disable a native
  manager without first proving the overlap with the pinned engine.
- For a failed lock update, inspect Python compatibility, bounded requirements,
  source policy, and the pinned `uv` diagnostics. Do not hand-edit `uv.lock`,
  relax frozen checks, or merge only the manifest.
- For runtime or marker drift, compare `.python-version`, `requires-python`, lock
  markers, and the hosted runner. One platform's selected set is not the entire
  cross-platform lock.
- For graph or evidence mismatch, compare normalized package names and exact
  versions across lock selection, installation, dependency tree, SBOM, and
  audit. Do not report missing evidence as zero findings.
- For Action changes, retain Node 24-ready majors, least-privilege permissions,
  bounded artifact upload, and `actionlint` success.
- For anonymous lookup failures, preserve sanitized manager/package diagnostics
  and require a hosted run with normal Mend/GitHub access. Never place tokens or
  package-index credentials in logs or artifacts.

## Human activation and hosted acceptance

Preparation alone does not activate automation. A human administrator must:

1. Publish the reviewed changes to trusted `main`. Record the source branch and
   exact commit from `git branch --show-current` and `git rev-parse HEAD`; do not
   substitute an uncommitted base revision. Retain successful CI and Python
   Dependency Evidence run URLs plus the complete seven-day artifact and every
   file in the allowlist in `docs/dependency-contract.md`.
2. Add `budgetanalyzer/budget-analyzer-api-tests` to the free Mend Renovate
   Community App scope with `main` as the default base, Scan and Alert behavior,
   and automerge disabled.
3. Enable GitHub's dependency graph and Dependabot alerts while leaving
   Dependabot version updates and automatic security-update pull requests off.
4. Retain a sanitized hosted Renovate run proving effective shared-preset
   resolution, native PEP 621/uv, Python runtime, tool-pin, and Action extraction,
   weekly lock maintenance, expected package sources, and no fatal errors.
5. Compare the GitHub dependency graph and alerts with `uv.lock` and the
   repository evidence by normalized package name and exact version. Record any
   hosted graph omission; do not claim complete GitHub coverage when the full
   lock graph is not ingested.

Acceptance passes only when the published revision, both hosted workflows, the
complete evidence artifact, hosted Renovate extraction/lookup logs, and GitHub
settings/graph review all agree. The effective managers must remain PEP 621
with its `uv.lock`, `pyenv`, GitHub Actions, Renovate config, and the two narrow
tool-pin regex managers. Any partial graph, failed lookup, unexpected duplicate
or source, incomplete artifact, lock mismatch, or missing workflow result keeps
the repository in pre-activation status. Do not copy credentials, session data,
or administrator secrets into the handoff.
