from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from api_tests.dependency_contract import (
    PROJECT_NAME,
    DependencyContract,
    LockedRequirement,
    canonicalize_name,
    pinned_uv_executable,
    validate_contract_files,
)
from api_tests.local_bootstrap import (
    frozen_lock_check_command,
    frozen_sync_check_command,
    sanitized_environment,
)

INVENTORY_SCHEMA: Final = 1


@dataclass(frozen=True, order=True)
class InstalledPackage:
    name: str
    version: str


@dataclass(frozen=True)
class ProjectPackage:
    name: str
    version: str
    editable: bool


class DependencyInventoryError(RuntimeError):
    """A failure to prove or serialize exact lock/environment parity."""


def write_dependency_inventory(
    path: Path,
    *,
    root: Path,
    python_executable: Path,
) -> None:
    """Verify the frozen environment and write stable machine-readable evidence."""
    contract, issues = validate_contract_files(root)
    if contract is None or issues:
        details = "; ".join(issues) if issues else "dependency contract is incomplete"
        raise DependencyInventoryError(details)
    runtime_version = platform.python_version()
    if runtime_version != contract.python_pin:
        raise DependencyInventoryError(
            f"inventory requires Python {contract.python_pin}, got {runtime_version}"
        )

    uv_executable = pinned_uv_executable(root, contract)
    _verify_uv_version(root, uv_executable, contract.uv_version)
    _require_success(
        _run_command(frozen_lock_check_command(uv_executable, python_executable), root),
        "uv.lock is stale or invalid",
    )
    parity_command = frozen_sync_check_command(uv_executable)
    _require_success(
        _run_command(parity_command, root),
        "installed environment does not exactly match the frozen lock",
    )
    installed_result = _run_command(
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
    )
    _require_success(installed_result, "could not inventory the installed environment")
    project, installed = _parse_installed_packages(installed_result.stdout, root, contract)

    lock_packages = [asdict(item) for item in contract.locked_requirements]
    document: Mapping[str, object] = {
        "schema_version": INVENTORY_SCHEMA,
        "python": {
            "implementation": platform.python_implementation(),
            "version": runtime_version,
        },
        "uv": {"version": contract.uv_version},
        "lock": {
            "path": "uv.lock",
            "sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
            "packages": lock_packages,
        },
        "project": asdict(project),
        "installed_packages": [asdict(item) for item in installed],
        "parity": {
            "status": "exact",
            "checker": "uv sync --check --frozen --all-groups",
            "selected_installed_third_party_count": len(installed),
            "universal_locked_third_party_count": len(lock_packages),
        },
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_installed_packages(
    raw: str,
    root: Path,
    contract: DependencyContract,
) -> tuple[ProjectPackage, tuple[InstalledPackage, ...]]:
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DependencyInventoryError(f"uv package inventory is invalid JSON: {exc}") from exc
    if not isinstance(decoded, list):
        raise DependencyInventoryError("uv package inventory must be a JSON array")

    project: ProjectPackage | None = None
    installed: set[InstalledPackage] = set()
    locked = set(contract.locked_requirements)
    seen_names: set[str] = set()
    for index, value in enumerate(decoded):
        if not isinstance(value, dict):
            raise DependencyInventoryError(f"installed package record {index} must be an object")
        name = value.get("name")
        version = value.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise DependencyInventoryError(
                f"installed package record {index} must contain name and version strings"
            )
        normalized_name = canonicalize_name(name)
        if normalized_name in seen_names:
            raise DependencyInventoryError(
                f"installed package inventory contains duplicate name {normalized_name!r}"
            )
        seen_names.add(normalized_name)
        editable_location = value.get("editable_project_location")
        if normalized_name == PROJECT_NAME:
            if not isinstance(editable_location, str):
                raise DependencyInventoryError("first-party project is not installed editable")
            if Path(editable_location).resolve() != root.resolve():
                raise DependencyInventoryError(
                    "first-party editable project points outside the repository"
                )
            project = ProjectPackage(normalized_name, version, editable=True)
            continue
        if editable_location is not None:
            raise DependencyInventoryError(
                f"third-party package {normalized_name!r} must not be editable"
            )
        package = InstalledPackage(normalized_name, version)
        if LockedRequirement(package.name, package.version) not in locked:
            raise DependencyInventoryError(
                f"installed package is absent from uv.lock at this version: "
                f"{package.name}=={package.version}"
            )
        installed.add(package)
    if project is None:
        raise DependencyInventoryError("first-party editable project is missing")
    return project, tuple(sorted(installed))


def _verify_uv_version(root: Path, executable: Path, expected: str) -> None:
    result = _run_command((str(executable), "--version"), root)
    _require_success(result, f"pinned uv {expected} is unavailable")
    if result.stdout.strip().split(" (")[0] != f"uv {expected}":
        raise DependencyInventoryError(f"expected uv {expected}, got {result.stdout.strip()!r}")


def _require_success(result: subprocess.CompletedProcess[str], message: str) -> None:
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise DependencyInventoryError(f"{message}: {detail}")


def _run_command(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        env=sanitized_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
