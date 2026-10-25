from __future__ import annotations

import os
import re
import subprocess
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PYPI_INDEX: Final = "https://pypi.org/simple"
PROJECT_NAME: Final = "budget-analyzer-api-tests"
REQUIRED_DEPENDENCY_GROUPS: Final = frozenset({"build", "dev"})
_REQUIREMENT_PATTERN: Final = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9._,-]+\])?"
    r"(?P<specifier>(?:===|==|~=|!=|<=|>=|<|>).+)$"
)
_EXACT_VERSION_PATTERN: Final = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[A-Za-z0-9.-]+)?$")
_BOUND_VERSION_PATTERN: Final = re.compile(r"^[0-9]+(?:\.[0-9]+)*(?:[A-Za-z0-9.-]+)?$")
_PINNED_EXPORT_PATTERN: Final = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^;\\\s]+)"
)

CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class LockedRequirement:
    name: str
    version: str


@dataclass(frozen=True)
class DependencyContract:
    python_pin: str
    requires_python: str
    uv_version: str
    pip_audit_version: str
    direct_requirements: tuple[str, ...]
    locked_requirements: tuple[LockedRequirement, ...]


def pinned_uv_executable(root: Path, contract: DependencyContract) -> Path:
    """Return the repository-local path for the declared bootstrap tool."""
    return root / ".dependency-tools" / f"uv-{contract.uv_version}" / "bin" / "uv"


def canonicalize_name(name: str) -> str:
    """Return the normalized distribution name used for comparisons."""
    return re.sub(r"[-_.]+", "-", name).lower()


def validate_contract_files(root: Path) -> tuple[DependencyContract | None, tuple[str, ...]]:
    """Validate dependency declarations and the checked-in lock without running uv."""
    issues: list[str] = []
    pyproject = _load_toml(root / "pyproject.toml", issues)
    lock = _load_toml(root / "uv.lock", issues)
    python_pin = _load_python_pin(root / ".python-version", issues)
    if pyproject is None or lock is None or python_pin is None:
        return None, tuple(issues)

    project = _mapping_member(pyproject, "project", "pyproject.toml", issues)
    build_system = _mapping_member(pyproject, "build-system", "pyproject.toml", issues)
    groups = _mapping_member(pyproject, "dependency-groups", "pyproject.toml", issues)
    tool = _mapping_member(pyproject, "tool", "pyproject.toml", issues)
    if project is None or build_system is None or groups is None or tool is None:
        return None, tuple(issues)

    requires_python = _string_member(project, "requires-python", "project", issues)
    expected_python = _expected_python_constraint(python_pin, issues)
    if (
        requires_python is not None
        and expected_python is not None
        and requires_python != expected_python
    ):
        issues.append(
            "project.requires-python must be "
            f"{expected_python!r} for .python-version {python_pin}, got {requires_python!r}"
        )

    if "optional-dependencies" in project:
        issues.append(
            "project.optional-dependencies must not contain development tools; "
            "use dependency-groups"
        )

    declared: list[str] = []
    project_dependencies = _string_list_member(project, "dependencies", "project", issues)
    build_dependencies = _string_list_member(build_system, "requires", "build-system", issues)
    declared.extend(project_dependencies)
    declared.extend(build_dependencies)

    group_names = set(groups)
    missing_groups = REQUIRED_DEPENDENCY_GROUPS - group_names
    if missing_groups:
        issues.append(
            "dependency-groups is missing required group(s): " + ", ".join(sorted(missing_groups))
        )
    for group_name, group_value in groups.items():
        if not isinstance(group_value, list):
            issues.append(f"dependency-groups.{group_name} must be an array")
            continue
        for index, item in enumerate(group_value):
            location = f"dependency-groups.{group_name}[{index}]"
            if isinstance(item, str):
                declared.append(item)
                continue
            if isinstance(item, dict):
                include_group = item.get("include-group")
                if (
                    len(item) != 1
                    or not isinstance(include_group, str)
                    or include_group not in groups
                ):
                    issues.append(f"{location} must name an existing include-group")
                continue
            issues.append(f"{location} must be a requirement or include-group")

    declared_names: set[str] = set()
    for declaration in declared:
        name = _validate_requirement(declaration, issues)
        if name is not None:
            declared_names.add(name)

    dependency_tool = _mapping_member(tool, "dependency-contract", "tool", issues)
    uv_tool = _mapping_member(tool, "uv", "tool", issues)
    if dependency_tool is None or uv_tool is None:
        return None, tuple(issues)
    uv_version = _exact_tool_pin(dependency_tool, "uv", issues)
    pip_audit_version = _exact_tool_pin(dependency_tool, "pip-audit", issues)

    default_groups = _string_list_member(uv_tool, "default-groups", "tool.uv", issues)
    if default_groups != ["dev"]:
        issues.append("tool.uv.default-groups must be exactly ['dev']")
    no_isolation = _string_list_member(uv_tool, "no-build-isolation-package", "tool.uv", issues)
    if no_isolation != [PROJECT_NAME]:
        issues.append(f"tool.uv.no-build-isolation-package must be exactly ['{PROJECT_NAME}']")
    if "sources" in uv_tool:
        issues.append("tool.uv.sources is not allowed; dependencies must come from standard PyPI")

    locked_requirements = _validate_lock(lock, python_pin, declared_names, issues)

    if (
        requires_python is None
        or uv_version is None
        or pip_audit_version is None
        or expected_python is None
    ):
        return None, tuple(issues)
    return (
        DependencyContract(
            python_pin=python_pin,
            requires_python=requires_python,
            uv_version=uv_version,
            pip_audit_version=pip_audit_version,
            direct_requirements=tuple(declared),
            locked_requirements=locked_requirements,
        ),
        tuple(issues),
    )


def validate_uv_resolution(
    root: Path,
    contract: DependencyContract,
    uv_executable: str,
    *,
    runner: CommandRunner | None = None,
) -> tuple[str, ...]:
    """Use the pinned uv to prove lock freshness and hashed exact export output."""
    run = runner or _run_command
    issues: list[str] = []
    try:
        version_result = run((uv_executable, "--version"), root)
    except FileNotFoundError:
        return (
            f"pinned uv {contract.uv_version} is unavailable at {uv_executable!r}; "
            "install it with ./run-tests or "
            "PYTHONPATH=src python -m api_tests.local_bootstrap",
        )

    expected_version_prefix = f"uv {contract.uv_version}"
    if version_result.returncode != 0:
        issues.append(f"could not run {uv_executable!r}: {_command_error(version_result)}")
        return tuple(issues)
    if version_result.stdout.strip().split(" (")[0] != expected_version_prefix:
        issues.append(f"expected {expected_version_prefix}, got {version_result.stdout.strip()!r}")
        return tuple(issues)

    lock_result = run(
        (
            uv_executable,
            "lock",
            "--check",
            "--python",
            contract.python_pin,
            "--no-sources",
        ),
        root,
    )
    if lock_result.returncode != 0:
        issues.append("uv.lock is stale or invalid: " + _command_error(lock_result))
        return tuple(issues)

    export_result = run(
        (
            uv_executable,
            "export",
            "--frozen",
            "--all-groups",
            "--no-emit-project",
            "--no-annotate",
            "--no-header",
            "--format",
            "requirements.txt",
            "--python",
            contract.python_pin,
        ),
        root,
    )
    if export_result.returncode != 0:
        issues.append(
            "could not export the frozen dependency graph: " + _command_error(export_result)
        )
        return tuple(issues)
    issues.extend(_validate_hashed_export(export_result.stdout, contract.locked_requirements))
    return tuple(issues)


def check_dependency_contract(
    root: Path,
    uv_executable: str | None = None,
    *,
    runner: CommandRunner | None = None,
) -> tuple[str, ...]:
    """Validate declaration policy, lock structure, freshness, and frozen export.

    Without an explicit executable, use the repository-pinned uv from the contract.
    """
    contract, file_issues = validate_contract_files(root)
    if contract is None or file_issues:
        return file_issues
    if uv_executable is None:
        uv_executable = str(pinned_uv_executable(root, contract))
    return validate_uv_resolution(root, contract, uv_executable, runner=runner)


def _load_toml(path: Path, issues: list[str]) -> Mapping[str, object] | None:
    try:
        parsed: object = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        issues.append(f"missing required file: {path.name}")
        return None
    except tomllib.TOMLDecodeError as exc:
        issues.append(f"invalid {path.name}: {exc}")
        return None
    if not isinstance(parsed, dict):
        issues.append(f"{path.name} must contain a TOML table")
        return None
    return parsed


def _load_python_pin(path: Path, issues: list[str]) -> str | None:
    try:
        pin = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        issues.append("missing required file: .python-version")
        return None
    parts = pin.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        issues.append(".python-version must contain one exact three-part Python version")
        return None
    return pin


def _expected_python_constraint(pin: str, issues: list[str]) -> str | None:
    major_text, minor_text, _ = pin.split(".")
    major = int(major_text)
    minor = int(minor_text)
    if (major, minor) != (3, 12):
        issues.append(f".python-version must select the supported 3.12 line, got {pin}")
        return None
    return f">={major}.{minor},<{major}.{minor + 1}"


def _mapping_member(
    value: Mapping[str, object], key: str, location: str, issues: list[str]
) -> Mapping[str, object] | None:
    member = value.get(key)
    if not isinstance(member, dict):
        issues.append(f"{location}.{key} must be a table")
        return None
    return member


def _string_member(
    value: Mapping[str, object], key: str, location: str, issues: list[str]
) -> str | None:
    member = value.get(key)
    if not isinstance(member, str) or not member:
        issues.append(f"{location}.{key} must be a nonempty string")
        return None
    return member


def _string_list_member(
    value: Mapping[str, object], key: str, location: str, issues: list[str]
) -> list[str]:
    member = value.get(key)
    if not isinstance(member, list) or not all(isinstance(item, str) for item in member):
        issues.append(f"{location}.{key} must be an array of strings")
        return []
    return list(member)


def _exact_tool_pin(tools: Mapping[str, object], name: str, issues: list[str]) -> str | None:
    version = _string_member(tools, name, "tool.dependency-contract", issues)
    if version is not None and _EXACT_VERSION_PATTERN.fullmatch(version) is None:
        issues.append(f"tool.dependency-contract.{name} must be an exact version")
        return None
    return version


def _validate_requirement(declaration: str, issues: list[str]) -> str | None:
    match = _REQUIREMENT_PATTERN.fullmatch(declaration)
    if match is None or "@" in declaration or ";" in declaration:
        issues.append(
            f"direct requirement must use a bounded registry declaration: {declaration!r}"
        )
        return None
    specifier = match.group("specifier").replace(" ", "")
    clauses = [clause for clause in specifier.split(",") if clause]
    exact = [
        clause for clause in clauses if clause.startswith("==") and not clause.startswith("===")
    ]
    lower = [clause for clause in clauses if clause.startswith(">=")]
    upper = [clause for clause in clauses if clause.startswith("<") and not clause.startswith("<=")]
    valid_exact = (
        len(clauses) == 1
        and len(exact) == 1
        and _BOUND_VERSION_PATTERN.fullmatch(exact[0][2:]) is not None
    )
    valid_interval = (
        len(lower) == 1
        and len(upper) == 1
        and len(clauses) == 2
        and _BOUND_VERSION_PATTERN.fullmatch(lower[0][2:]) is not None
        and _BOUND_VERSION_PATTERN.fullmatch(upper[0][1:]) is not None
    )
    if not valid_exact and not valid_interval:
        issues.append(
            "direct requirement must have one inclusive lower and one upper bound, "
            f"or one exact pin: {declaration!r}"
        )
    return canonicalize_name(match.group("name"))


def _validate_lock(
    lock: Mapping[str, object],
    python_pin: str,
    declared_names: set[str],
    issues: list[str],
) -> tuple[LockedRequirement, ...]:
    major, minor, _ = python_pin.split(".")
    expected_lock_python = f"=={major}.{minor}.*"
    if lock.get("requires-python") != expected_lock_python:
        issues.append(
            f"uv.lock requires-python must be {expected_lock_python!r}, "
            f"got {lock.get('requires-python')!r}"
        )
    packages = lock.get("package")
    if not isinstance(packages, list) or not packages:
        issues.append("uv.lock must contain a nonempty package array")
        return ()

    locked_names: set[str] = set()
    locked_requirements: set[LockedRequirement] = set()
    project_package: Mapping[str, object] | None = None
    for index, package_value in enumerate(packages):
        location = f"uv.lock package[{index}]"
        if not isinstance(package_value, dict):
            issues.append(f"{location} must be a table")
            continue
        package: Mapping[str, object] = package_value
        name = package.get("name")
        version = package.get("version")
        source = package.get("source")
        if not isinstance(name, str) or not isinstance(version, str):
            issues.append(f"{location} must have exact name and version strings")
            continue
        normalized_name = canonicalize_name(name)
        locked_names.add(normalized_name)
        if not isinstance(source, dict):
            issues.append(f"{location} must declare a package source")
            continue
        if normalized_name == PROJECT_NAME:
            project_package = package
            if source != {"editable": "."}:
                issues.append(f"{PROJECT_NAME} must be the sole editable '.' lock source")
            continue
        if source != {"registry": PYPI_INDEX}:
            issues.append(f"{name} has an unexpected package source: {source!r}")
            continue
        locked_requirements.add(LockedRequirement(normalized_name, version))
        artifacts: list[object] = []
        sdist = package.get("sdist")
        if sdist is not None:
            artifacts.append(sdist)
        wheels = package.get("wheels")
        if isinstance(wheels, list):
            artifacts.extend(wheels)
        elif wheels is not None:
            issues.append(f"{name} wheels must be an array")
        if not artifacts:
            issues.append(f"{name} has no locked distribution artifacts")
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                issues.append(f"{name} has malformed artifact metadata")
                continue
            digest = artifact.get("hash")
            if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
                issues.append(f"{name} has an artifact without a SHA-256 integrity record")

    missing_locked = declared_names - locked_names
    if missing_locked:
        issues.append(
            "uv.lock is missing direct requirement(s): " + ", ".join(sorted(missing_locked))
        )
    if project_package is None:
        issues.append(f"uv.lock is missing editable project package {PROJECT_NAME!r}")
        return tuple(sorted(locked_requirements, key=lambda item: (item.name, item.version)))
    dev_dependencies = project_package.get("dev-dependencies")
    if not isinstance(dev_dependencies, dict):
        issues.append("uv.lock project package is missing dependency group metadata")
        return tuple(sorted(locked_requirements, key=lambda item: (item.name, item.version)))
    missing_lock_groups = REQUIRED_DEPENDENCY_GROUPS - set(dev_dependencies)
    if missing_lock_groups:
        issues.append(
            "uv.lock project package is missing dependency group(s): "
            + ", ".join(sorted(missing_lock_groups))
        )
    return tuple(sorted(locked_requirements, key=lambda item: (item.name, item.version)))


def _validate_hashed_export(
    export: str, locked_requirements: Sequence[LockedRequirement]
) -> tuple[str, ...]:
    issues: list[str] = []
    blocks: list[str] = []
    current: list[str] = []
    for line in export.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if line[0].isspace():
            if not current:
                issues.append("frozen export begins with an orphaned continuation line")
            else:
                current.append(line.strip())
            continue
        if current:
            blocks.append(" ".join(current))
        current = [line.strip()]
    if current:
        blocks.append(" ".join(current))
    if not blocks:
        return ("frozen export did not contain any third-party requirements",)
    exported_requirements: set[LockedRequirement] = set()
    for block in blocks:
        match = _PINNED_EXPORT_PATTERN.match(block)
        if match is None:
            issues.append(f"frozen export requirement is not exact: {block!r}")
            continue
        if "--hash=sha256:" not in block:
            issues.append(f"frozen export requirement has no SHA-256 hash: {match.group('name')}")
        exported_requirements.add(
            LockedRequirement(canonicalize_name(match.group("name")), match.group("version"))
        )
    if issues:
        return tuple(issues)
    expected_requirements = set(locked_requirements)
    missing = expected_requirements - exported_requirements
    unexpected = exported_requirements - expected_requirements
    if missing:
        issues.append("frozen export is missing locked requirement(s): " + _format_locked(missing))
    if unexpected:
        issues.append(
            "frozen export has unexpected requirement(s) or version(s): "
            + _format_locked(unexpected)
        )
    return tuple(issues)


def _format_locked(requirements: set[LockedRequirement]) -> str:
    return ", ".join(
        f"{requirement.name}=={requirement.version}"
        for requirement in sorted(requirements, key=lambda item: (item.name, item.version))
    )


def _run_command(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in (
        "UV_DEFAULT_INDEX",
        "UV_EXTRA_INDEX_URL",
        "UV_FIND_LINKS",
        "UV_INDEX",
        "UV_INDEX_URL",
    ):
        environment.pop(name, None)
    return subprocess.run(
        command,
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _command_error(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
