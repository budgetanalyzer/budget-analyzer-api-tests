from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest

from api_tests import dependency_contract
from api_tests.config import repository_root
from api_tests.dependency_contract import (
    DependencyContract,
    LockedRequirement,
    validate_contract_files,
    validate_uv_resolution,
)
from api_tests.tools.dependency_contract import main


def test_checked_in_dependency_contract_files_are_valid() -> None:
    root = repository_root()
    contract, issues = validate_contract_files(root)

    python_pin = (root / ".python-version").read_text(encoding="utf-8").strip()
    uv_pin = _checked_in_tool_pin(root, "uv")
    pip_audit_pin = _checked_in_tool_pin(root, "pip-audit")
    assert issues == ()
    assert contract is not None
    for pin in (python_pin, uv_pin, pip_audit_pin):
        assert re.fullmatch(r"\d+\.\d+\.\d+", pin), pin
    assert contract.python_pin == python_pin
    assert contract.uv_version == uv_pin
    assert contract.pip_audit_version == pip_audit_pin


def _checked_in_tool_pin(root: Path, name: str) -> str:
    with (root / "pyproject.toml").open("rb") as file:
        document = tomllib.load(file)
    tool = document["tool"]
    assert isinstance(tool, dict)
    dependency_tool = tool["dependency-contract"]
    assert isinstance(dependency_tool, dict)
    pin = dependency_tool[name]
    assert isinstance(pin, str)
    return pin


def test_unbounded_direct_requirement_is_rejected(tmp_path: Path) -> None:
    root = _copy_contract_files(tmp_path)
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    (root / "pyproject.toml").write_text(
        pyproject.replace('"httpx>=0.28.1,<0.29"', '"httpx"'),
        encoding="utf-8",
    )

    _, issues = validate_contract_files(root)

    assert any("bounded registry declaration" in issue and "httpx" in issue for issue in issues)


def test_arbitrary_exact_requirement_is_rejected(tmp_path: Path) -> None:
    root = _copy_contract_files(tmp_path)
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    (root / "pyproject.toml").write_text(
        pyproject.replace('"httpx>=0.28.1,<0.29"', '"httpx===0.28.1"'),
        encoding="utf-8",
    )

    _, issues = validate_contract_files(root)

    assert any(
        "inclusive lower and one upper bound" in issue and "httpx" in issue for issue in issues
    )


def test_python_runtime_interval_must_match_exact_pin(tmp_path: Path) -> None:
    root = _copy_contract_files(tmp_path)
    (root / ".python-version").write_text("3.13.0\n", encoding="utf-8")

    _, issues = validate_contract_files(root)

    assert any("must select the supported 3.12 line" in issue for issue in issues)

    (root / ".python-version").write_text("3.12.15\n", encoding="utf-8")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    (root / "pyproject.toml").write_text(
        pyproject.replace('requires-python = ">=3.12,<3.13"', 'requires-python = ">=3.12"'),
        encoding="utf-8",
    )
    _, issues = validate_contract_files(root)
    assert any("project.requires-python must be" in issue for issue in issues)


def test_missing_required_dependency_group_is_rejected(tmp_path: Path) -> None:
    root = _copy_contract_files(tmp_path)
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    (root / "pyproject.toml").write_text(
        pyproject.replace('build = [\n  "setuptools>=84.0.0,<85",\n]\n', ""),
        encoding="utf-8",
    )

    _, issues = validate_contract_files(root)

    assert any("missing required group(s): build" in issue for issue in issues)


def test_nonstandard_lock_source_is_rejected(tmp_path: Path) -> None:
    root = _copy_contract_files(tmp_path)
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    (root / "uv.lock").write_text(
        lock.replace(
            'source = { registry = "https://pypi.org/simple" }',
            'source = { registry = "https://packages.example.invalid/simple" }',
            1,
        ),
        encoding="utf-8",
    )

    _, issues = validate_contract_files(root)

    assert any("unexpected package source" in issue for issue in issues)


def test_stale_lock_result_is_rejected() -> None:
    contract = _contract()

    def runner(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
        del root
        if command[1] == "--version":
            return _result(command, stdout="uv 0.13.0 (test)\n")
        return _result(command, returncode=2, stderr="lock needs to be updated\n")

    issues = validate_uv_resolution(Path("/repository"), contract, "uv", runner=runner)

    assert issues == ("uv.lock is stale or invalid: lock needs to be updated",)


def test_frozen_export_requires_exact_versions_and_hashes() -> None:
    contract = _contract()

    def runner(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
        del root
        if command[1] == "--version":
            return _result(command, stdout="uv 0.13.0 (test)\n")
        if command[1] == "lock":
            return _result(command)
        return _result(command, stdout="example>=1.0\n")

    issues = validate_uv_resolution(Path("/repository"), contract, "uv", runner=runner)

    assert issues == ("frozen export requirement is not exact: 'example>=1.0'",)


def test_frozen_export_accepts_exact_hashed_requirements() -> None:
    contract = _contract()

    def runner(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
        del root
        if command[1] == "--version":
            return _result(command, stdout="uv 0.13.0 (test)\n")
        if command[1] == "lock":
            return _result(command)
        return _result(
            command,
            stdout=(
                "example==1.2.3 \\\n"
                "    --hash=sha256:0123456789abcdef0123456789abcdef"
                "0123456789abcdef0123456789abcdef\n"
            ),
        )

    assert validate_uv_resolution(Path("/repository"), contract, "uv", runner=runner) == ()


def test_frozen_export_must_match_locked_names_and_versions() -> None:
    contract = _contract()

    def runner(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
        del root
        if command[1] == "--version":
            return _result(command, stdout="uv 0.13.0 (test)\n")
        if command[1] == "lock":
            return _result(command)
        return _result(
            command,
            stdout=(
                "example==1.2.4 \\\n"
                "    --hash=sha256:0123456789abcdef0123456789abcdef"
                "0123456789abcdef0123456789abcdef\n"
            ),
        )

    issues = validate_uv_resolution(Path("/repository"), contract, "uv", runner=runner)

    assert issues == (
        "frozen export is missing locked requirement(s): example==1.2.3",
        "frozen export has unexpected requirement(s) or version(s): example==1.2.4",
    )


def test_cli_defaults_to_repository_pinned_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _copy_contract_files(tmp_path)
    contract = _copied_contract(root)
    executables = _install_fake_runner(monkeypatch, contract)

    exit_code = main(["--root", str(root)])

    expected = str(root / ".dependency-tools" / f"uv-{contract.uv_version}" / "bin" / "uv")
    assert exit_code == 0
    assert executables
    assert set(executables) == {expected}
    assert "Dependency contract is valid" in capsys.readouterr().out


def test_cli_explicit_uv_overrides_pinned_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_contract_files(tmp_path)
    executables = _install_fake_runner(monkeypatch, _copied_contract(root))

    exit_code = main(["--root", str(root), "--uv", "/custom/uv"])

    assert exit_code == 0
    assert executables
    assert set(executables) == {"/custom/uv"}


def test_cli_reports_invalid_contract_files_without_running_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _copy_contract_files(tmp_path)
    (root / ".python-version").write_text("not-a-version\n", encoding="utf-8")
    executables = _install_fake_runner(monkeypatch, None)

    exit_code = main(["--root", str(root)])

    assert exit_code == 1
    assert executables == []
    error = capsys.readouterr().err
    assert "dependency contract error: .python-version must contain" in error
    assert "Traceback" not in error


def test_cli_reports_missing_pinned_uv_with_bootstrap_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _copy_contract_files(tmp_path)
    contract = _copied_contract(root)

    def runner(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
        del root
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(dependency_contract, "_run_command", runner)

    exit_code = main(["--root", str(root)])

    expected = root / ".dependency-tools" / f"uv-{contract.uv_version}" / "bin" / "uv"
    error = capsys.readouterr().err
    assert exit_code == 1
    assert f"unavailable at {str(expected)!r}" in error
    assert "./run-tests" in error
    assert "PYTHONPATH=src python -m api_tests.local_bootstrap" in error


def _copied_contract(root: Path) -> DependencyContract:
    contract, issues = validate_contract_files(root)
    assert issues == ()
    assert contract is not None
    return contract


def _install_fake_runner(
    monkeypatch: pytest.MonkeyPatch, contract: DependencyContract | None
) -> list[str]:
    executables: list[str] = []

    def runner(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
        del root
        assert contract is not None
        executables.append(command[0])
        if command[1] == "--version":
            return _result(command, stdout=f"uv {contract.uv_version} (test)\n")
        if command[1] == "lock":
            return _result(command)
        return _result(
            command,
            stdout="".join(
                f"{requirement.name}=={requirement.version} \\\n    --hash=sha256:{'0' * 64}\n"
                for requirement in contract.locked_requirements
            ),
        )

    monkeypatch.setattr(dependency_contract, "_run_command", runner)
    return executables


def _copy_contract_files(tmp_path: Path) -> Path:
    source_root = repository_root()
    for name in ("pyproject.toml", ".python-version", "uv.lock"):
        (tmp_path / name).write_text(
            (source_root / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return tmp_path


def _contract() -> DependencyContract:
    return DependencyContract(
        python_pin="3.12.15",
        requires_python=">=3.12,<3.13",
        uv_version="0.13.0",
        pip_audit_version="2.10.1",
        direct_requirements=("example>=1.0,<2",),
        locked_requirements=(LockedRequirement("example", "1.2.3"),),
    )


def _result(
    command: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)
