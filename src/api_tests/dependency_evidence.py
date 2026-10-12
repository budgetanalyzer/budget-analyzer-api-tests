from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
import sysconfig
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from api_tests.dependency_contract import (
    PROJECT_NAME,
    DependencyContract,
    canonicalize_name,
    validate_contract_files,
)
from api_tests.local_bootstrap import (
    frozen_lock_check_command,
    frozen_sync_check_command,
    sanitized_environment,
)

EVIDENCE_SCHEMA: Final = 1
PYPI_INDEX: Final = "https://pypi.org/simple"
SETUP_OUTPUTS: Final = ("bootstrap.log", "scanner-install.log")
LEGACY_OUTPUTS: Final = ("dependency-tree.txt",)
GENERATED_OUTPUTS: Final = (
    "diagnostics.json",
    "metadata.json",
    "lock-export.txt",
    "lock-selection.json",
    "installed-packages.json",
    "dependency-tree.json",
    "playwright-browser-metadata.json",
    "pip-audit.json",
    "pip-audit.txt",
    "sbom.cdx.json",
    "scanner-status.json",
    "scanner.stderr.log",
    "parity.json",
    "completion.json",
)
SUCCESSFUL_OUTPUTS: Final = SETUP_OUTPUTS + GENERATED_OUTPUTS

_SECRET_ASSIGNMENT: Final = re.compile(
    r"(?i)\b(authorization|cookie|password|session|token|api[_-]?key)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_URL_USERINFO: Final = re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@")
_KNOWN_TOKEN: Final = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
)


@dataclass(frozen=True, order=True)
class PackageRecord:
    name: str
    version: str


@dataclass(frozen=True)
class VersionMismatch:
    name: str
    expected: str
    actual: str


@dataclass(frozen=True)
class PackageComparison:
    source: str
    status: str
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    version_mismatches: tuple[VersionMismatch, ...]


@dataclass(frozen=True)
class AuditReport:
    packages: tuple[PackageRecord, ...]
    vulnerability_count: int
    vulnerable_package_count: int
    document: Mapping[str, object]


@dataclass(frozen=True)
class SbomReport:
    packages: tuple[PackageRecord, ...]
    vulnerability_count: int
    document: Mapping[str, object]


@dataclass(frozen=True)
class DependencyTree:
    packages: tuple[PackageRecord, ...]
    document: Mapping[str, object]


class DependencyEvidenceError(RuntimeError):
    """An operational failure that makes dependency evidence incomplete."""


def redact_text(value: str) -> str:
    """Remove common credential forms before diagnostics are persisted."""
    redacted = _URL_USERINFO.sub(r"\1<redacted>@", value)
    redacted = _KNOWN_TOKEN.sub("<redacted>", redacted)
    return _SECRET_ASSIGNMENT.sub(r"\1\2<redacted>", redacted)


def normalize_package_records(
    records: Sequence[tuple[str, str]], *, source: str
) -> tuple[PackageRecord, ...]:
    """Normalize package names and reject incomplete or duplicate records."""
    normalized: dict[str, str] = {}
    duplicates: set[str] = set()
    for name, version in records:
        normalized_name = canonicalize_name(name.strip())
        normalized_version = version.strip()
        if not normalized_name or not normalized_version:
            raise DependencyEvidenceError(f"{source} contains an empty package name or version")
        if normalized_name in normalized:
            duplicates.add(normalized_name)
        else:
            normalized[normalized_name] = normalized_version
    if duplicates:
        raise DependencyEvidenceError(
            f"{source} contains duplicate normalized package name(s): "
            + ", ".join(sorted(duplicates))
        )
    return tuple(PackageRecord(name, version) for name, version in sorted(normalized.items()))


def compare_exact_packages(
    expected: Sequence[PackageRecord],
    actual: Sequence[PackageRecord],
    *,
    source: str,
) -> PackageComparison:
    """Return an exact normalized name/version comparison against the lock selection."""
    expected_versions = {package.name: package.version for package in expected}
    actual_versions = {package.name: package.version for package in actual}
    missing = tuple(sorted(expected_versions.keys() - actual_versions.keys()))
    extra = tuple(sorted(actual_versions.keys() - expected_versions.keys()))
    mismatches = tuple(
        VersionMismatch(name, expected_versions[name], actual_versions[name])
        for name in sorted(expected_versions.keys() & actual_versions.keys())
        if expected_versions[name] != actual_versions[name]
    )
    status = "exact" if not missing and not extra and not mismatches else "mismatch"
    return PackageComparison(source, status, missing, extra, mismatches)


def require_exact_comparisons(comparisons: Sequence[PackageComparison]) -> None:
    """Fail when any graph representation differs from the selected lock set."""
    mismatched = [comparison for comparison in comparisons if comparison.status != "exact"]
    if not mismatched:
        return
    details = []
    for comparison in mismatched:
        versions = [mismatch.name for mismatch in comparison.version_mismatches]
        details.append(
            f"{comparison.source}: missing={list(comparison.missing)}, "
            f"extra={list(comparison.extra)}, version_mismatch={versions}"
        )
    raise DependencyEvidenceError("package parity failed: " + "; ".join(details))


def parse_lock_export(raw: str) -> tuple[PackageRecord, ...]:
    """Select active hashed requirements from a universal uv requirements export."""
    from packaging.requirements import InvalidRequirement, Requirement

    records: list[tuple[str, str]] = []
    for block in _requirement_blocks(raw):
        first_line = block[0].removesuffix("\\").strip()
        if not any("--hash=sha256:" in line for line in block[1:]):
            raise DependencyEvidenceError(
                f"lock export requirement has no SHA-256 hash: {first_line}"
            )
        try:
            requirement = Requirement(first_line)
        except InvalidRequirement as exc:
            raise DependencyEvidenceError(
                f"lock export contains an invalid requirement: {first_line!r}"
            ) from exc
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != "==" or "*" in specifiers[0].version:
            raise DependencyEvidenceError(
                f"lock export requirement is not exactly pinned: {first_line!r}"
            )
        if requirement.marker is None or requirement.marker.evaluate():
            records.append((requirement.name, specifiers[0].version))
    if not records:
        raise DependencyEvidenceError("lock export selected no third-party packages")
    return normalize_package_records(records, source="selected lock export")


def parse_installed_inventory(
    raw: str, *, root: Path
) -> tuple[Mapping[str, object], tuple[PackageRecord, ...]]:
    """Parse uv's installed inventory and isolate the one editable first-party project."""
    value = _decode_json(raw, source="installed package inventory")
    if not isinstance(value, list):
        raise DependencyEvidenceError("installed package inventory must be a JSON array")
    project: Mapping[str, object] | None = None
    records: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise DependencyEvidenceError(
                f"installed package inventory record {index} must be an object"
            )
        name = item.get("name")
        version = item.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise DependencyEvidenceError(
                f"installed package inventory record {index} needs name and version strings"
            )
        normalized_name = canonicalize_name(name)
        editable_location = item.get("editable_project_location")
        if normalized_name == PROJECT_NAME:
            if project is not None:
                raise DependencyEvidenceError("installed inventory contains duplicate project")
            if not isinstance(editable_location, str) or Path(editable_location).resolve() != root:
                raise DependencyEvidenceError(
                    "first-party project must be editable from the repository root"
                )
            project = {"name": normalized_name, "version": version, "editable": True}
            continue
        if editable_location is not None:
            raise DependencyEvidenceError(
                f"third-party package {normalized_name!r} must not be editable"
            )
        records.append((name, version))
    if project is None:
        raise DependencyEvidenceError("installed inventory is missing the editable project")
    return project, normalize_package_records(records, source="installed package inventory")


def parse_audit_report(raw: str, *, exit_code: int) -> AuditReport:
    """Validate complete pip-audit JSON and classify findings separately from failure."""
    value = _decode_json(raw, source="pip-audit JSON")
    if not isinstance(value, dict):
        raise DependencyEvidenceError("pip-audit JSON must be an object")
    dependencies = value.get("dependencies")
    if not isinstance(dependencies, list):
        raise DependencyEvidenceError("pip-audit JSON has no dependency array")
    records: list[tuple[str, str]] = []
    vulnerability_count = 0
    vulnerable_packages = 0
    for index, item in enumerate(dependencies):
        if not isinstance(item, dict):
            raise DependencyEvidenceError(f"pip-audit dependency {index} must be an object")
        name = item.get("name")
        version = item.get("version")
        vulnerabilities = item.get("vulns")
        if (
            not isinstance(name, str)
            or not isinstance(version, str)
            or not isinstance(vulnerabilities, list)
        ):
            raise DependencyEvidenceError(
                f"pip-audit dependency {index} needs name, version, and vulns fields"
            )
        if not all(isinstance(vulnerability, dict) for vulnerability in vulnerabilities):
            raise DependencyEvidenceError(
                f"pip-audit dependency {canonicalize_name(name)!r} has malformed findings"
            )
        records.append((name, version))
        vulnerability_count += len(vulnerabilities)
        vulnerable_packages += bool(vulnerabilities)
    _validate_scanner_exit(
        format_name="pip-audit JSON",
        exit_code=exit_code,
        vulnerability_count=vulnerability_count,
    )
    return AuditReport(
        normalize_package_records(records, source="pip-audit dependencies"),
        vulnerability_count,
        vulnerable_packages,
        value,
    )


def parse_sbom_report(raw: str, *, exit_code: int) -> SbomReport:
    """Validate pip-audit's CycloneDX 1.4 JSON and return its package set."""
    value = _decode_json(raw, source="CycloneDX SBOM")
    if not isinstance(value, dict):
        raise DependencyEvidenceError("CycloneDX SBOM must be an object")
    if value.get("bomFormat") != "CycloneDX" or value.get("specVersion") != "1.4":
        raise DependencyEvidenceError("SBOM must be CycloneDX JSON specification 1.4")
    components = value.get("components")
    if not isinstance(components, list):
        raise DependencyEvidenceError("CycloneDX SBOM has no component array")
    records: list[tuple[str, str]] = []
    for index, item in enumerate(components):
        if not isinstance(item, dict):
            raise DependencyEvidenceError(f"CycloneDX component {index} must be an object")
        name = item.get("name")
        version = item.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise DependencyEvidenceError(
                f"CycloneDX component {index} needs name and version strings"
            )
        records.append((name, version))
    vulnerabilities = value.get("vulnerabilities", [])
    if not isinstance(vulnerabilities, list) or not all(
        isinstance(vulnerability, dict) for vulnerability in vulnerabilities
    ):
        raise DependencyEvidenceError("CycloneDX vulnerabilities must be an array of objects")
    _validate_scanner_exit(
        format_name="CycloneDX SBOM",
        exit_code=exit_code,
        vulnerability_count=len(vulnerabilities),
    )
    return SbomReport(
        normalize_package_records(records, source="CycloneDX components"),
        len(vulnerabilities),
        value,
    )


def parse_dependency_tree(raw: str) -> DependencyTree:
    """Normalize uv's selected graph into a stable path-free machine-readable tree."""
    value = _decode_json(raw, source="uv dependency tree")
    if not isinstance(value, dict):
        raise DependencyEvidenceError("uv dependency tree must be an object")
    resolution = value.get("resolution")
    roots = value.get("roots")
    if not isinstance(resolution, dict) or not isinstance(roots, list):
        raise DependencyEvidenceError("uv dependency tree needs resolution and roots fields")

    stable_ids: dict[str, str] = {}
    node_values: dict[str, Mapping[str, object]] = {}
    package_records: list[tuple[str, str]] = []
    for original_id, item in resolution.items():
        if not isinstance(original_id, str) or not isinstance(item, dict):
            raise DependencyEvidenceError("uv dependency tree has a malformed resolution node")
        stable_id, kind = _tree_node_identity(item)
        if stable_id in node_values:
            raise DependencyEvidenceError(f"uv dependency tree has duplicate node {stable_id!r}")
        stable_ids[original_id] = stable_id
        node_values[stable_id] = {"kind": kind, "item": item}
        if kind == "package":
            name = item.get("name")
            version = item.get("version")
            if not isinstance(name, str) or not isinstance(version, str):
                raise DependencyEvidenceError(
                    f"uv dependency tree package {stable_id!r} needs name and version"
                )
            package_records.append((name, version))

    nodes: list[Mapping[str, object]] = []
    for stable_id, node_value in sorted(node_values.items()):
        item = node_value["item"]
        if not isinstance(item, dict):
            raise DependencyEvidenceError("uv dependency tree contains an invalid node")
        dependencies = item.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise DependencyEvidenceError(
                f"uv dependency tree node {stable_id!r} has invalid dependencies"
            )
        stable_dependencies: list[str] = []
        for dependency in dependencies:
            if not isinstance(dependency, dict) or not isinstance(dependency.get("id"), str):
                raise DependencyEvidenceError(
                    f"uv dependency tree node {stable_id!r} has a malformed dependency"
                )
            dependency_id = dependency["id"]
            if dependency_id not in stable_ids:
                raise DependencyEvidenceError(
                    f"uv dependency tree node {stable_id!r} references an unknown node"
                )
            stable_dependencies.append(stable_ids[dependency_id])
        nodes.append(
            {
                "id": stable_id,
                "kind": node_value["kind"],
                "dependencies": sorted(stable_dependencies),
            }
        )

    stable_roots: list[str] = []
    for root in roots:
        if not isinstance(root, dict) or not isinstance(root.get("id"), str):
            raise DependencyEvidenceError("uv dependency tree contains a malformed root")
        root_id = root["id"]
        if root_id not in stable_ids:
            raise DependencyEvidenceError("uv dependency tree contains an unknown root")
        stable_roots.append(stable_ids[root_id])
    packages = normalize_package_records(package_records, source="uv dependency tree packages")
    return DependencyTree(
        packages,
        {
            "schema_version": EVIDENCE_SCHEMA,
            "source": "uv tree --frozen --all-groups --format json",
            "roots": sorted(stable_roots),
            "nodes": nodes,
        },
    )


def validate_complete_outputs(output_dir: Path, *, include_completion: bool = True) -> None:
    """Enforce the semantic allowlist for a successful evidence run."""
    expected = set(SUCCESSFUL_OUTPUTS)
    if not include_completion:
        expected.remove("completion.json")
    present = {path.name for path in output_dir.iterdir() if path.is_file()}
    missing = sorted(expected - present)
    unexpected = sorted(present - expected)
    empty = sorted(name for name in expected & present if not (output_dir / name).read_bytes())
    if missing or unexpected or empty:
        raise DependencyEvidenceError(
            "evidence allowlist is incomplete: "
            f"missing={missing}, unexpected={unexpected}, empty={empty}"
        )


def generate_dependency_evidence(
    *,
    root: Path,
    output_dir: Path,
    python_executable: Path,
    uv_executable: Path,
    scanner_executable: Path,
) -> None:
    """Generate and validate one complete selected-graph evidence set."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in GENERATED_OUTPUTS + LEGACY_OUTPUTS:
        (output_dir / name).unlink(missing_ok=True)
    stage = "initialize"
    _write_diagnostics(output_dir, status="running", stage=stage)
    try:
        contract = _load_contract(root)
        stage = "verify tools and frozen environment"
        _verify_runtime_and_tools(
            root=root,
            contract=contract,
            python_executable=python_executable,
            uv_executable=uv_executable,
            scanner_executable=scanner_executable,
        )

        stage = "export selected lock graph"
        export_result = _require_command(
            _run(
                (
                    str(uv_executable),
                    "export",
                    "--frozen",
                    "--all-groups",
                    "--no-emit-project",
                    "--no-annotate",
                    "--no-header",
                    "--format",
                    "requirements.txt",
                    "--python",
                    str(python_executable),
                    "--no-managed-python",
                    "--no-python-downloads",
                ),
                root,
            ),
            "could not export the frozen lock",
        )
        lock_export = export_result.stdout
        lock_packages = parse_lock_export(lock_export)
        (output_dir / "lock-export.txt").write_text(lock_export, encoding="utf-8")
        _write_json(
            output_dir / "lock-selection.json",
            {
                "schema_version": EVIDENCE_SCHEMA,
                "selection": "current runner environment markers",
                "packages": _package_documents(lock_packages),
            },
        )

        stage = "generate dependency tree"
        tree_result = _require_command(
            _run(
                (
                    str(uv_executable),
                    "tree",
                    "--frozen",
                    "--all-groups",
                    "--format",
                    "json",
                    "--preview-features",
                    "json-output",
                    "--python-version",
                    contract.python_pin,
                    "--no-python-downloads",
                ),
                root,
            ),
            "could not generate the frozen dependency tree",
        )
        dependency_tree = parse_dependency_tree(tree_result.stdout)
        _write_json(output_dir / "dependency-tree.json", dependency_tree.document)

        stage = "inventory installed environment"
        inventory_result = _require_command(
            _run(
                (
                    str(uv_executable),
                    "pip",
                    "list",
                    "--format",
                    "json",
                    "--python",
                    str(python_executable),
                    "--no-managed-python",
                    "--no-python-downloads",
                ),
                root,
            ),
            "could not inventory the installed environment",
        )
        project, installed_packages = parse_installed_inventory(
            inventory_result.stdout, root=root.resolve()
        )
        _write_json(
            output_dir / "installed-packages.json",
            {
                "schema_version": EVIDENCE_SCHEMA,
                "project_exclusion": project,
                "packages": _package_documents(installed_packages),
            },
        )

        stage = "derive Playwright browser metadata"
        browser_metadata = _playwright_browser_metadata()
        _write_json(output_dir / "playwright-browser-metadata.json", browser_metadata)

        stage = "scan selected dependency graph"
        audit_result = _run_scanner(
            scanner_executable,
            lock_export_path=output_dir / "lock-export.txt",
            output_format="json",
            root=root,
        )
        sbom_result = _run_scanner(
            scanner_executable,
            lock_export_path=output_dir / "lock-export.txt",
            output_format="cyclonedx-json",
            root=root,
        )
        (output_dir / "scanner.stderr.log").write_text(
            "[pip-audit-json]\n"
            + redact_text(audit_result.stderr)
            + "\n[cyclonedx-json]\n"
            + redact_text(sbom_result.stderr),
            encoding="utf-8",
        )
        _write_json(
            output_dir / "scanner-status.json",
            {
                "schema_version": EVIDENCE_SCHEMA,
                "pip_audit_json_exit_code": audit_result.returncode,
                "cyclonedx_json_exit_code": sbom_result.returncode,
                "accepted_exit_codes": [0, 1],
            },
        )
        audit = parse_audit_report(audit_result.stdout, exit_code=audit_result.returncode)
        sbom = parse_sbom_report(sbom_result.stdout, exit_code=sbom_result.returncode)
        if audit.vulnerability_count != sbom.vulnerability_count:
            raise DependencyEvidenceError(
                "pip-audit JSON and CycloneDX vulnerability counts disagree: "
                f"{audit.vulnerability_count} != {sbom.vulnerability_count}"
            )
        _write_json(output_dir / "pip-audit.json", audit.document)
        _write_json(output_dir / "sbom.cdx.json", sbom.document)
        (output_dir / "pip-audit.txt").write_text(_human_findings(audit.document), encoding="utf-8")

        stage = "enforce exact graph parity"
        comparisons = (
            compare_exact_packages(
                lock_packages, dependency_tree.packages, source="dependency tree"
            ),
            compare_exact_packages(lock_packages, installed_packages, source="installed"),
            compare_exact_packages(lock_packages, audit.packages, source="pip-audit"),
            compare_exact_packages(lock_packages, sbom.packages, source="CycloneDX SBOM"),
        )
        _write_json(
            output_dir / "parity.json",
            {
                "schema_version": EVIDENCE_SCHEMA,
                "status": "exact"
                if all(item.status == "exact" for item in comparisons)
                else "mismatch",
                "reference": "platform-selected uv lock export",
                "package_count": len(lock_packages),
                "comparisons": [asdict(item) for item in comparisons],
                "exclusions": [
                    {
                        "scope": "project",
                        "name": PROJECT_NAME,
                        "reason": "first-party editable package",
                    },
                    {
                        "scope": "bootstrap environment",
                        "name": "uv",
                        "reason": "isolated pinned bootstrap tool",
                    },
                    {
                        "scope": "scanner environment",
                        "name": "pip-audit and its tool dependencies",
                        "reason": "isolated pinned scanner tool",
                    },
                ],
            },
        )
        require_exact_comparisons(comparisons)

        stage = "write metadata and completion"
        _write_json(
            output_dir / "metadata.json",
            _metadata_document(
                root=root,
                contract=contract,
                package_count=len(lock_packages),
            ),
        )
        _write_diagnostics(output_dir, status="complete", stage="complete")
        validate_complete_outputs(output_dir, include_completion=False)
        completed_outputs = {
            name: _sha256(output_dir / name)
            for name in SUCCESSFUL_OUTPUTS
            if name != "completion.json"
        }
        _write_json(
            output_dir / "completion.json",
            {
                "schema_version": EVIDENCE_SCHEMA,
                "status": "complete",
                "generated_at": _timestamp(),
                "selected_package_count": len(lock_packages),
                "vulnerable_package_count": audit.vulnerable_package_count,
                "vulnerability_count": audit.vulnerability_count,
                "findings_are_gating": False,
                "outputs": completed_outputs,
            },
        )
        validate_complete_outputs(output_dir)
    except (DependencyEvidenceError, OSError, subprocess.SubprocessError) as exc:
        message = redact_text(str(exc))
        (output_dir / "completion.json").unlink(missing_ok=True)
        _write_diagnostics(output_dir, status="failed", stage=stage, error=message)
        raise DependencyEvidenceError(message) from exc


def write_failure_diagnostics(output_dir: Path, *, stage: str, error: str) -> None:
    """Persist a redacted setup failure before the project generator can run."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_diagnostics(output_dir, status="failed", stage=stage, error=redact_text(error))


def _load_contract(root: Path) -> DependencyContract:
    contract, issues = validate_contract_files(root)
    if contract is None or issues:
        details = "; ".join(issues) if issues else "dependency contract is incomplete"
        raise DependencyEvidenceError(details)
    return contract


def _verify_runtime_and_tools(
    *,
    root: Path,
    contract: DependencyContract,
    python_executable: Path,
    uv_executable: Path,
    scanner_executable: Path,
) -> None:
    if platform.python_version() != contract.python_pin:
        raise DependencyEvidenceError(
            f"evidence requires Python {contract.python_pin}, got {platform.python_version()}"
        )
    uv_version = _require_command(_run((str(uv_executable), "--version"), root), "uv failed")
    if uv_version.stdout.strip().split(" (")[0] != f"uv {contract.uv_version}":
        raise DependencyEvidenceError(
            f"expected uv {contract.uv_version}, got {uv_version.stdout.strip()!r}"
        )
    scanner_version = _require_command(
        _run((str(scanner_executable), "--version"), root), "pip-audit failed"
    )
    if scanner_version.stdout.strip() != f"pip-audit {contract.pip_audit_version}":
        raise DependencyEvidenceError(
            f"expected pip-audit {contract.pip_audit_version}, "
            f"got {scanner_version.stdout.strip()!r}"
        )
    _require_command(
        _run(frozen_lock_check_command(uv_executable, python_executable), root),
        "uv.lock is stale or invalid",
    )
    _require_command(
        _run(frozen_sync_check_command(uv_executable), root),
        "installed environment does not match the frozen lock",
    )


def _run_scanner(
    scanner_executable: Path,
    *,
    lock_export_path: Path,
    output_format: str,
    root: Path,
) -> subprocess.CompletedProcess[str]:
    return _run(
        (
            str(scanner_executable),
            "--strict",
            "--progress-spinner",
            "off",
            "--vulnerability-service",
            "pypi",
            "--requirement",
            str(lock_export_path),
            "--no-deps",
            "--disable-pip",
            "--format",
            output_format,
        ),
        root,
    )


def _playwright_browser_metadata() -> Mapping[str, object]:
    try:
        distribution = importlib.metadata.distribution("playwright")
    except importlib.metadata.PackageNotFoundError as exc:
        raise DependencyEvidenceError("installed Playwright distribution is missing") from exc
    browser_path = Path(
        str(distribution.locate_file("playwright/driver/package/browsers.json"))
    ).resolve()
    value = _decode_json(
        browser_path.read_text(encoding="utf-8"), source="Playwright browsers.json"
    )
    if not isinstance(value, dict) or not isinstance(value.get("browsers"), list):
        raise DependencyEvidenceError("Playwright browsers.json has no browser array")
    browsers: list[Mapping[str, object]] = []
    chromium_count = 0
    for index, item in enumerate(value["browsers"]):
        if not isinstance(item, dict):
            raise DependencyEvidenceError(f"Playwright browser record {index} must be an object")
        name = item.get("name")
        revision = item.get("revision")
        if not isinstance(name, str) or not isinstance(revision, str):
            raise DependencyEvidenceError(
                f"Playwright browser record {index} needs name and revision strings"
            )
        chromium_count += name == "chromium"
        record: dict[str, object] = {"name": name, "revision": revision}
        for field in ("browserVersion", "installByDefault", "revisionOverrides", "title"):
            if field in item:
                record[field] = item[field]
        browsers.append(record)
    if chromium_count != 1:
        raise DependencyEvidenceError("Playwright must declare exactly one Chromium revision")
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "playwright_version": distribution.version,
        "source": "installed Playwright package browsers.json",
        "browser_installation_performed": False,
        "browsers": browsers,
    }


def _tree_node_identity(item: Mapping[str, object]) -> tuple[str, str]:
    kind = item.get("kind")
    name = item.get("name")
    version = item.get("version")
    if kind == "workspace":
        return "workspace", "workspace"
    if not isinstance(name, str) or not isinstance(version, str):
        raise DependencyEvidenceError("uv dependency tree node needs name and version strings")
    normalized_name = canonicalize_name(name)
    if kind == "package":
        source = item.get("source")
        if not isinstance(source, dict):
            raise DependencyEvidenceError("uv dependency tree package needs a source")
        if "editable" in source:
            if normalized_name != PROJECT_NAME:
                raise DependencyEvidenceError("uv dependency tree has an unexpected editable")
            return f"project:{normalized_name}=={version}", "project"
        return f"package:{normalized_name}=={version}", "package"
    if isinstance(kind, dict) and isinstance(kind.get("group"), str):
        return f"group:{kind['group']}", "group"
    if isinstance(kind, dict) and isinstance(kind.get("extra"), str):
        extra = kind["extra"]
        return f"extra:{normalized_name}[{extra}]=={version}", "extra"
    raise DependencyEvidenceError(f"uv dependency tree has an unsupported node kind: {kind!r}")


def _metadata_document(
    *,
    root: Path,
    contract: DependencyContract,
    package_count: int,
) -> Mapping[str, object]:
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "generated_at": _timestamp(),
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "platform": sysconfig.get_platform(),
        },
        "tools": {
            "uv": {"version": contract.uv_version, "environment": "isolated bootstrap"},
            "pip-audit": {
                "version": contract.pip_audit_version,
                "declared_version": contract.pip_audit_version,
                "environment": "isolated scanner",
            },
        },
        "project_environment": ".venv (frozen, all dependency groups)",
        "package_index": PYPI_INDEX,
        "vulnerability_service": "pypi",
        "selected_package_count": package_count,
        "inputs": {
            ".python-version": _sha256(root / ".python-version"),
            "pyproject.toml": _sha256(root / "pyproject.toml"),
            "uv.lock": _sha256(root / "uv.lock"),
        },
    }


def _human_findings(document: Mapping[str, object]) -> str:
    dependencies = document.get("dependencies")
    if not isinstance(dependencies, list):
        raise DependencyEvidenceError("pip-audit JSON has no dependency array")
    rows: list[tuple[str, str, str, str]] = []
    for item in dependencies:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        version = item.get("version")
        vulnerabilities = item.get("vulns")
        if (
            not isinstance(name, str)
            or not isinstance(version, str)
            or not isinstance(vulnerabilities, list)
        ):
            continue
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                continue
            identifier = vulnerability.get("id")
            fixes = vulnerability.get("fix_versions", [])
            if not isinstance(identifier, str) or not isinstance(fixes, list):
                raise DependencyEvidenceError("pip-audit finding has malformed id or fixes")
            fix_text = ", ".join(str(fix) for fix in fixes) if fixes else "none"
            rows.append((canonicalize_name(name), version, identifier, fix_text))
    lines = [
        "pip-audit dependency findings",
        "",
        f"Audited packages: {len(dependencies)}",
        f"Vulnerability records: {len(rows)}",
        "Findings are retained but are not merge-gating.",
        "",
    ]
    if not rows:
        lines.append("No known vulnerabilities found.")
    else:
        lines.extend("\t".join(row) for row in sorted(rows))
    return "\n".join(lines) + "\n"


def _requirement_blocks(raw: str) -> tuple[tuple[str, ...], ...]:
    blocks: list[tuple[str, ...]] = []
    current: list[str] = []
    for line in raw.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if line[0].isspace():
            if not current:
                raise DependencyEvidenceError("lock export starts with an orphan continuation")
            current.append(line.strip())
            continue
        if current:
            blocks.append(tuple(current))
        current = [line.strip()]
    if current:
        blocks.append(tuple(current))
    return tuple(blocks)


def _validate_scanner_exit(*, format_name: str, exit_code: int, vulnerability_count: int) -> None:
    expected = 1 if vulnerability_count else 0
    if exit_code not in (0, 1) or exit_code != expected:
        raise DependencyEvidenceError(
            f"{format_name} returned {exit_code} for {vulnerability_count} findings; "
            f"expected {expected}"
        )


def _decode_json(raw: str, *, source: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DependencyEvidenceError(f"{source} is malformed JSON: {exc}") from exc


def _package_documents(packages: Sequence[PackageRecord]) -> list[Mapping[str, str]]:
    return [asdict(package) for package in packages]


def _write_diagnostics(
    output_dir: Path, *, status: str, stage: str, error: str | None = None
) -> None:
    document: dict[str, object] = {
        "schema_version": EVIDENCE_SCHEMA,
        "updated_at": _timestamp(),
        "status": status,
        "stage": stage,
    }
    if error is not None:
        document["error"] = error
    _write_json(output_dir / "diagnostics.json", document)


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        env=sanitized_environment(),
        check=False,
        capture_output=True,
        text=True,
    )


def _require_command(
    result: subprocess.CompletedProcess[str], message: str
) -> subprocess.CompletedProcess[str]:
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise DependencyEvidenceError(f"{message}: {redact_text(detail)}")
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()
