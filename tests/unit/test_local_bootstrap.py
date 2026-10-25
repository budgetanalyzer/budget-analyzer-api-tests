from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from api_tests import local_bootstrap
from api_tests.config import repository_root
from api_tests.dependency_contract import DependencyContract, LockedRequirement


def test_first_sync_writes_marker_and_unchanged_environment_is_reused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _copy_contract_files(tmp_path)
    commands: list[tuple[str, ...]] = []
    _mock_uv(monkeypatch, commands)

    assert not local_bootstrap.bootstrap_environment(
        root,
        bootstrap_python_executable=Path("/runtime/python3.12"),
        bootstrap_runtime_version="3.12.3",
    )
    assert [command[1] for command in commands] == ["lock", "sync", "sync"]
    assert "--check" not in commands[1]
    assert "--check" in commands[2]
    assert all("--managed-python" in command for command in commands)
    assert all("--no-python-downloads" in command for command in commands)

    commands.clear()
    assert local_bootstrap.bootstrap_environment(
        root,
        bootstrap_python_executable=Path("/runtime/python3.12"),
        bootstrap_runtime_version="3.12.3",
    )
    assert [command[1] for command in commands] == ["lock", "sync"]
    assert "--check" in commands[1]


@pytest.mark.parametrize("changed_file", ["pyproject.toml", "uv.lock", ".python-version"])
def test_contract_input_change_invalidates_reuse_marker(
    changed_file: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _copy_contract_files(tmp_path)
    commands: list[tuple[str, ...]] = []
    _mock_uv(monkeypatch, commands)
    python_path = Path("/runtime/python3.12")
    assert not local_bootstrap.bootstrap_environment(
        root,
        bootstrap_python_executable=python_path,
        bootstrap_runtime_version="3.12.3",
    )

    path = root / changed_file
    if changed_file == ".python-version":
        path.write_text("3.12.14\n", encoding="utf-8")
    else:
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    commands.clear()

    assert not local_bootstrap.bootstrap_environment(
        root,
        bootstrap_python_executable=python_path,
        bootstrap_runtime_version="3.12.3",
    )
    assert [command[1] for command in commands] == ["lock", "sync", "sync"]


def test_tool_pin_change_invalidates_tool_and_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _copy_contract_files(tmp_path)
    commands: list[tuple[str, ...]] = []
    tool_versions: list[str] = []

    def fake_ensure(
        selected_root: Path,
        contract: DependencyContract,
        python_executable: Path,
    ) -> Path:
        del selected_root, python_executable
        tool_versions.append(contract.uv_version)
        return Path(f"/tools/uv-{contract.uv_version}/bin/uv")

    monkeypatch.setattr(local_bootstrap, "ensure_pinned_uv", fake_ensure)
    _mock_managed_python(monkeypatch)
    monkeypatch.setattr(local_bootstrap, "_run_command", _successful_runner(commands))
    monkeypatch.setattr(
        local_bootstrap, "_environment_has_only_locked_packages", lambda root, contract, uv: True
    )
    python_path = Path("/runtime/python3.12")
    assert not local_bootstrap.bootstrap_environment(
        root,
        bootstrap_python_executable=python_path,
        bootstrap_runtime_version="3.12.3",
    )

    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    current_pin = _uv_pin(pyproject)
    replacement_pin = "0.0.1"
    assert current_pin != replacement_pin
    updated = pyproject.replace(f'uv = "{current_pin}"', f'uv = "{replacement_pin}"')
    assert updated != pyproject
    (root / "pyproject.toml").write_text(updated, encoding="utf-8")
    commands.clear()
    assert not local_bootstrap.bootstrap_environment(
        root,
        bootstrap_python_executable=python_path,
        bootstrap_runtime_version="3.12.3",
    )

    assert tool_versions == [current_pin, replacement_pin]
    assert [command[1] for command in commands] == ["lock", "sync", "sync"]


def test_stale_lock_fails_before_synchronization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _copy_contract_files(tmp_path)
    monkeypatch.setattr(
        local_bootstrap,
        "ensure_pinned_uv",
        lambda selected_root, contract, python_executable: Path("/tools/uv"),
    )
    _mock_managed_python(monkeypatch)
    commands: list[tuple[str, ...]] = []

    def fake_run(command: Sequence[str], selected_root: Path) -> subprocess.CompletedProcess[str]:
        del selected_root
        commands.append(tuple(command))
        return _result(command, returncode=2, stderr="lock needs to be updated")

    monkeypatch.setattr(local_bootstrap, "_run_command", fake_run)

    with pytest.raises(local_bootstrap.BootstrapError, match=r"uv\.lock is stale"):
        local_bootstrap.bootstrap_environment(
            root,
            bootstrap_python_executable=Path("/runtime/python3.12"),
            bootstrap_runtime_version="3.12.3",
        )

    assert [command[1] for command in commands] == ["lock"]


def test_failed_synchronization_does_not_write_readiness_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _copy_contract_files(tmp_path)
    monkeypatch.setattr(
        local_bootstrap,
        "ensure_pinned_uv",
        lambda selected_root, contract, python_executable: Path("/tools/uv"),
    )
    _mock_managed_python(monkeypatch)

    def fake_run(command: Sequence[str], selected_root: Path) -> subprocess.CompletedProcess[str]:
        del selected_root
        if command[1] == "lock":
            return _result(command)
        return _result(command, returncode=1, stderr="wheel unavailable")

    monkeypatch.setattr(local_bootstrap, "_run_command", fake_run)

    with pytest.raises(local_bootstrap.BootstrapError, match="synchronization failed"):
        local_bootstrap.bootstrap_environment(
            root,
            bootstrap_python_executable=Path("/runtime/python3.12"),
            bootstrap_runtime_version="3.12.3",
        )

    assert not (root / ".venv" / local_bootstrap.BOOTSTRAP_MARKER_NAME).exists()


def test_environment_mismatch_is_resynchronized_instead_of_reused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _copy_contract_files(tmp_path)
    commands: list[tuple[str, ...]] = []
    _mock_uv(monkeypatch, commands)
    python_path = Path("/runtime/python3.12")
    assert not local_bootstrap.bootstrap_environment(
        root,
        bootstrap_python_executable=python_path,
        bootstrap_runtime_version="3.12.3",
    )
    commands.clear()
    sync_checks = 0

    def fake_run(command: Sequence[str], selected_root: Path) -> subprocess.CompletedProcess[str]:
        nonlocal sync_checks
        del selected_root
        commands.append(tuple(command))
        if command[1] == "sync" and "--check" in command:
            sync_checks += 1
            if sync_checks == 1:
                return _result(command, returncode=1, stderr="environment is stale")
        return _result(command)

    monkeypatch.setattr(local_bootstrap, "_run_command", fake_run)

    assert not local_bootstrap.bootstrap_environment(
        root,
        bootstrap_python_executable=python_path,
        bootstrap_runtime_version="3.12.3",
    )
    assert [command[1] for command in commands] == ["lock", "sync", "sync", "sync"]
    assert "--check" not in commands[2]


def test_extra_installed_package_prevents_reuse_even_when_uv_check_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _copy_contract_files(tmp_path)
    commands: list[tuple[str, ...]] = []
    _mock_uv(monkeypatch, commands)
    python_path = Path("/runtime/python3.12")
    assert not local_bootstrap.bootstrap_environment(
        root,
        bootstrap_python_executable=python_path,
        bootstrap_runtime_version="3.12.3",
    )
    commands.clear()
    parity = iter((False, True))
    monkeypatch.setattr(
        local_bootstrap,
        "_environment_has_only_locked_packages",
        lambda selected_root, contract, uv: next(parity),
    )

    assert not local_bootstrap.bootstrap_environment(
        root,
        bootstrap_python_executable=python_path,
        bootstrap_runtime_version="3.12.3",
    )
    assert [command[1] for command in commands] == ["lock", "sync", "sync", "sync"]


def test_bootstrap_python_must_use_supported_minor_before_tool_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _copy_contract_files(tmp_path)
    ensure_called = False

    def fake_ensure(
        selected_root: Path,
        contract: DependencyContract,
        python_executable: Path,
    ) -> Path:
        nonlocal ensure_called
        del selected_root, contract, python_executable
        ensure_called = True
        return Path("/tools/uv")

    monkeypatch.setattr(local_bootstrap, "ensure_pinned_uv", fake_ensure)

    with pytest.raises(local_bootstrap.BootstrapError, match="required to bootstrap"):
        local_bootstrap.bootstrap_environment(
            root,
            bootstrap_python_executable=Path("/usr/bin/python3.11"),
            bootstrap_runtime_version="3.11.9",
        )
    assert not ensure_called


def test_managed_python_is_installed_resolved_and_verified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []

    def fake_run(command: Sequence[str], selected_root: Path) -> subprocess.CompletedProcess[str]:
        del selected_root
        commands.append(tuple(command))
        if command[1:3] == ("python", "list"):
            return _result(
                command,
                stdout=(
                    '[{"implementation":"cpython","version":"3.12.15",'
                    '"path":"/uv/managed/cpython-3.12.15/bin/python3.12"}]\n'
                ),
            )
        if command[1] == "-c":
            return _result(command, stdout="3.12.15\n")
        return _result(command)

    monkeypatch.setattr(local_bootstrap, "_run_command", fake_run)

    result = local_bootstrap.ensure_managed_python(tmp_path, _contract(), Path("/tools/uv"))

    assert result == Path("/uv/managed/cpython-3.12.15/bin/python3.12")
    assert commands[0] == (
        "/tools/uv",
        "python",
        "install",
        "3.12.15",
        "--managed-python",
        "--no-bin",
    )
    assert commands[1] == (
        "/tools/uv",
        "python",
        "list",
        "3.12.15",
        "--only-installed",
        "--managed-python",
        "--no-python-downloads",
        "--output-format",
        "json",
    )


def test_missing_pinned_uv_is_installed_in_separate_tool_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    matches = iter((False, True))
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        local_bootstrap,
        "_uv_version_matches",
        lambda executable, root, expected: next(matches),
    )
    monkeypatch.setattr(local_bootstrap, "_run_command", _successful_runner(commands))

    result = local_bootstrap.ensure_pinned_uv(
        tmp_path,
        _contract(),
        Path("/runtime/python3.12"),
    )

    assert result == tmp_path / ".dependency-tools" / "uv-0.13.0" / "bin" / "uv"
    assert commands[0][:4] == ("/runtime/python3.12", "-m", "venv", "--clear")
    assert "uv==0.13.0" in commands[1]
    assert "--isolated" in commands[1]
    assert local_bootstrap.PYPI_INDEX in commands[1]


def test_sanitized_environment_allows_only_explicit_managed_python_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UV_PYTHON", "caller-python")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS_JSON_URL", "https://example.invalid/python.json")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/caller/python")

    environment = local_bootstrap.sanitized_environment()

    assert "UV_PYTHON" not in environment
    assert "UV_PYTHON_DOWNLOADS" not in environment
    assert "UV_PYTHON_DOWNLOADS_JSON_URL" not in environment
    assert "UV_PYTHON_INSTALL_DIR" not in environment


def _copy_contract_files(tmp_path: Path) -> Path:
    for name in ("pyproject.toml", ".python-version", "uv.lock"):
        (tmp_path / name).write_text(
            (repository_root() / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return tmp_path


def _mock_uv(monkeypatch: pytest.MonkeyPatch, commands: list[tuple[str, ...]]) -> None:
    monkeypatch.setattr(
        local_bootstrap,
        "ensure_pinned_uv",
        lambda root, contract, python_executable: Path("/tools/uv"),
    )
    monkeypatch.setattr(local_bootstrap, "_run_command", _successful_runner(commands))
    _mock_managed_python(monkeypatch)
    monkeypatch.setattr(
        local_bootstrap, "_environment_has_only_locked_packages", lambda root, contract, uv: True
    )


def _mock_managed_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_bootstrap,
        "ensure_managed_python",
        lambda root, contract, uv_executable: Path(
            f"/uv/managed/cpython-{contract.python_pin}/bin/python3.12"
        ),
    )


def _successful_runner(
    commands: list[tuple[str, ...]],
) -> Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]:
    def run(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
        del root
        commands.append(tuple(command))
        return _result(command)

    return run


def _contract() -> DependencyContract:
    return DependencyContract(
        python_pin="3.12.15",
        requires_python=">=3.12,<3.13",
        uv_version="0.13.0",
        pip_audit_version="2.10.1",
        direct_requirements=("example>=1,<2",),
        locked_requirements=(LockedRequirement("example", "1.0"),),
    )


def _result(
    command: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def _uv_pin(pyproject: str) -> str:
    tool = tomllib.loads(pyproject)["tool"]
    assert isinstance(tool, dict)
    dependency_tool = tool["dependency-contract"]
    assert isinstance(dependency_tool, dict)
    pin = dependency_tool["uv"]
    assert isinstance(pin, str)
    return pin
