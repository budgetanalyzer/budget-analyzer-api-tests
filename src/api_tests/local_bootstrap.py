from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from api_tests.dependency_contract import (
    PROJECT_NAME,
    DependencyContract,
    canonicalize_name,
    validate_contract_files,
)

BOOTSTRAP_MARKER_SCHEMA: Final = 1
BOOTSTRAP_MARKER_NAME: Final = ".budget-analyzer-api-tests-frozen-ready.json"
PYPI_INDEX: Final = "https://pypi.org/simple"


@dataclass(frozen=True)
class BootstrapIdentity:
    schema_version: int
    python_version: str
    python_executable: str
    uv_version: str
    python_pin_sha256: str
    manifest_sha256: str
    lock_sha256: str


class BootstrapError(RuntimeError):
    """A user-actionable failure while preparing the frozen local environment."""


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print("local bootstrap does not accept arguments", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[2]
    try:
        reused = bootstrap_environment(
            root,
            python_executable=Path(sys.executable),
            runtime_version=platform.python_version(),
        )
    except BootstrapError as exc:
        print(f"run-tests dependency bootstrap failed: {exc}", file=sys.stderr)
        return 2
    if reused:
        print("Frozen Python environment matches uv.lock; reusing .venv.")
    else:
        print("Frozen Python environment synchronized from uv.lock.")
    return 0


def bootstrap_environment(
    root: Path,
    *,
    python_executable: Path,
    runtime_version: str,
) -> bool:
    """Prepare the exact locked environment and return whether it was reused unchanged."""
    contract, issues = validate_contract_files(root)
    if contract is None or issues:
        details = "; ".join(issues) if issues else "dependency contract is incomplete"
        raise BootstrapError(details)
    if runtime_version != contract.python_pin:
        raise BootstrapError(
            f"Python {contract.python_pin} is required by .python-version, but "
            f"{python_executable} is Python {runtime_version}; install the pinned interpreter "
            "or set BA_API_TEST_PYTHON to it (automatic Python downloads are disabled)"
        )

    uv_executable = ensure_pinned_uv(root, contract, python_executable)
    _check_lock(root, contract, uv_executable, python_executable)

    identity = _bootstrap_identity(root, contract, python_executable)
    marker_path = root / ".venv" / BOOTSTRAP_MARKER_NAME
    sync_command = _sync_command(uv_executable, python_executable)
    check_command = frozen_sync_check_command(uv_executable)
    if _marker_matches(marker_path, identity):
        check_result = _run_command(check_command, root)
        if check_result.returncode == 0 and _environment_has_only_locked_packages(
            root, contract, uv_executable
        ):
            return True

    marker_path.unlink(missing_ok=True)
    _remove_project_environment(root)
    sync_result = _run_command(sync_command, root)
    if sync_result.returncode != 0:
        raise BootstrapError("frozen uv synchronization failed: " + _command_error(sync_result))

    check_result = _run_command(check_command, root)
    if check_result.returncode != 0:
        raise BootstrapError(
            "uv completed synchronization but the environment does not match uv.lock: "
            + _command_error(check_result)
        )
    if not _environment_has_only_locked_packages(root, contract, uv_executable):
        raise BootstrapError(
            "uv completed synchronization but the installed package set contains a package "
            "or version outside uv.lock"
        )
    _write_marker(marker_path, identity)
    return False


def ensure_pinned_uv(
    root: Path,
    contract: DependencyContract,
    python_executable: Path,
) -> Path:
    """Install and verify the declared uv pin outside the project environment."""
    tool_root = root / ".dependency-tools" / f"uv-{contract.uv_version}"
    uv_executable = tool_root / "bin" / "uv"
    if _uv_version_matches(uv_executable, root, contract.uv_version):
        return uv_executable

    create_result = _run_command(
        (str(python_executable), "-m", "venv", "--clear", str(tool_root)), root
    )
    if create_result.returncode != 0:
        raise BootstrapError(
            "could not create the isolated uv environment: " + _command_error(create_result)
        )

    tool_python = tool_root / "bin" / "python"
    install_result = _run_command(
        (
            str(tool_python),
            "-m",
            "pip",
            "install",
            "--isolated",
            "--disable-pip-version-check",
            "--index-url",
            PYPI_INDEX,
            "--no-deps",
            "--only-binary=:all:",
            f"uv=={contract.uv_version}",
        ),
        root,
    )
    if install_result.returncode != 0:
        raise BootstrapError(
            f"could not install pinned uv {contract.uv_version} from {PYPI_INDEX}: "
            + _command_error(install_result)
        )
    if not _uv_version_matches(uv_executable, root, contract.uv_version):
        raise BootstrapError(
            f"installed uv does not report the pinned version {contract.uv_version}"
        )
    return uv_executable


def pinned_uv_executable(root: Path, contract: DependencyContract) -> Path:
    """Return the repository-local path for the declared bootstrap tool."""
    return root / ".dependency-tools" / f"uv-{contract.uv_version}" / "bin" / "uv"


def frozen_sync_check_command(uv_executable: Path) -> tuple[str, ...]:
    """Return the non-mutating exact-environment parity command."""
    return (
        str(uv_executable),
        "sync",
        "--check",
        "--frozen",
        "--all-groups",
        "--no-python-downloads",
    )


def frozen_lock_check_command(uv_executable: Path, python_executable: Path) -> tuple[str, ...]:
    """Return the non-mutating manifest-versus-lock freshness command."""
    return (
        str(uv_executable),
        "lock",
        "--check",
        "--python",
        str(python_executable),
        "--no-managed-python",
        "--no-python-downloads",
        "--no-sources",
    )


def sanitized_environment() -> Mapping[str, str]:
    """Return an environment without caller-supplied package indexes or uv Python downloads."""
    environment = os.environ.copy()
    for name in (
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "UV_EXTRA_INDEX_URL",
        "UV_FIND_LINKS",
        "UV_INDEX",
        "UV_INDEX_URL",
        "UV_MANAGED_PYTHON",
        "UV_NO_DEFAULT_GROUPS",
        "UV_NO_DEV",
        "UV_NO_GROUP",
        "UV_NO_INSTALL_LOCAL",
        "UV_NO_INSTALL_PROJECT",
        "UV_NO_INSTALL_WORKSPACE",
        "UV_NO_MANAGED_PYTHON",
        "UV_PROJECT_ENVIRONMENT",
        "UV_PYTHON",
        "VIRTUAL_ENV",
    ):
        environment.pop(name, None)
    environment["UV_PYTHON_DOWNLOADS"] = "never"
    return environment


def _check_lock(
    root: Path,
    contract: DependencyContract,
    uv_executable: Path,
    python_executable: Path,
) -> None:
    result = _run_command(frozen_lock_check_command(uv_executable, python_executable), root)
    if result.returncode != 0:
        raise BootstrapError(
            "uv.lock is stale or invalid; refresh it only through the documented intentional "
            f"update workflow using uv {contract.uv_version}: {_command_error(result)}"
        )


def _sync_command(uv_executable: Path, python_executable: Path) -> tuple[str, ...]:
    return (
        str(uv_executable),
        "sync",
        "--frozen",
        "--all-groups",
        "--python",
        str(python_executable),
        "--no-managed-python",
        "--no-python-downloads",
    )


def _bootstrap_identity(
    root: Path, contract: DependencyContract, python_executable: Path
) -> BootstrapIdentity:
    return BootstrapIdentity(
        schema_version=BOOTSTRAP_MARKER_SCHEMA,
        python_version=contract.python_pin,
        python_executable=str(python_executable.resolve()),
        uv_version=contract.uv_version,
        python_pin_sha256=_sha256(root / ".python-version"),
        manifest_sha256=_sha256(root / "pyproject.toml"),
        lock_sha256=_sha256(root / "uv.lock"),
    )


def _marker_matches(path: Path, identity: BootstrapIdentity) -> bool:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    return value == asdict(identity)


def _environment_has_only_locked_packages(
    root: Path,
    contract: DependencyContract,
    uv_executable: Path,
) -> bool:
    result = _run_command(
        (
            str(uv_executable),
            "pip",
            "list",
            "--format",
            "json",
            "--python",
            str(root / ".venv" / "bin" / "python"),
            "--no-python-downloads",
        ),
        root,
    )
    if result.returncode != 0:
        return False
    try:
        decoded: object = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(decoded, list):
        return False
    locked = {(item.name, item.version) for item in contract.locked_requirements}
    project_found = False
    names: set[str] = set()
    for value in decoded:
        if not isinstance(value, dict):
            return False
        name = value.get("name")
        version = value.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            return False
        normalized_name = canonicalize_name(name)
        if normalized_name in names:
            return False
        names.add(normalized_name)
        if normalized_name == PROJECT_NAME:
            location = value.get("editable_project_location")
            if not isinstance(location, str) or Path(location).resolve() != root.resolve():
                return False
            project_found = True
        elif (normalized_name, version) not in locked:
            return False
    return project_found


def _remove_project_environment(root: Path) -> None:
    environment = root / ".venv"
    try:
        if environment.is_symlink() or environment.is_file():
            environment.unlink()
        elif environment.exists():
            shutil.rmtree(environment)
    except OSError as exc:
        raise BootstrapError(
            f"could not replace generated environment {environment}: {exc}"
        ) from exc


def _write_marker(path: Path, identity: BootstrapIdentity) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(identity), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _uv_version_matches(executable: Path, root: Path, expected: str) -> bool:
    if not executable.is_file():
        return False
    try:
        result = _run_command((str(executable), "--version"), root)
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip().split(" (")[0] == f"uv {expected}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_command(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        env=sanitized_environment(),
        check=False,
        capture_output=True,
        text=True,
    )


def _command_error(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"


if __name__ == "__main__":
    raise SystemExit(main())
