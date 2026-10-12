from __future__ import annotations

import subprocess
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
        python_executable=Path("/runtime/python3.12"),
        runtime_version="3.12.15",
    )
    assert [command[1] for command in commands] == ["lock", "sync", "sync"]
    assert "--check" not in commands[1]
    assert "--check" in commands[2]

    commands.clear()
    assert local_bootstrap.bootstrap_environment(
        root,
        python_executable=Path("/runtime/python3.12"),
        runtime_version="3.12.15",
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
        root, python_executable=python_path, runtime_version="3.12.15"
    )

    path = root / changed_file
    runtime_version = "3.12.15"
    if changed_file == ".python-version":
        path.write_text("3.12.14\n", encoding="utf-8")
        runtime_version = "3.12.14"
    else:
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    commands.clear()

    assert not local_bootstrap.bootstrap_environment(
        root, python_executable=python_path, runtime_version=runtime_version
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
    monkeypatch.setattr(local_bootstrap, "_run_command", _successful_runner(commands))
    monkeypatch.setattr(
        local_bootstrap, "_environment_has_only_locked_packages", lambda root, contract, uv: True
    )
    python_path = Path("/runtime/python3.12")
    assert not local_bootstrap.bootstrap_environment(
        root, python_executable=python_path, runtime_version="3.12.15"
    )

    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    (root / "pyproject.toml").write_text(
        pyproject.replace('uv = "0.13.0"', 'uv = "0.13.1"'),
        encoding="utf-8",
    )
    commands.clear()
    assert not local_bootstrap.bootstrap_environment(
        root, python_executable=python_path, runtime_version="3.12.15"
    )

    assert tool_versions == ["0.13.0", "0.13.1"]
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
    commands: list[tuple[str, ...]] = []

    def fake_run(command: Sequence[str], selected_root: Path) -> subprocess.CompletedProcess[str]:
        del selected_root
        commands.append(tuple(command))
        return _result(command, returncode=2, stderr="lock needs to be updated")

    monkeypatch.setattr(local_bootstrap, "_run_command", fake_run)

    with pytest.raises(local_bootstrap.BootstrapError, match=r"uv\.lock is stale"):
        local_bootstrap.bootstrap_environment(
            root,
            python_executable=Path("/runtime/python3.12"),
            runtime_version="3.12.15",
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

    def fake_run(command: Sequence[str], selected_root: Path) -> subprocess.CompletedProcess[str]:
        del selected_root
        if command[1] == "lock":
            return _result(command)
        return _result(command, returncode=1, stderr="wheel unavailable")

    monkeypatch.setattr(local_bootstrap, "_run_command", fake_run)

    with pytest.raises(local_bootstrap.BootstrapError, match="synchronization failed"):
        local_bootstrap.bootstrap_environment(
            root,
            python_executable=Path("/runtime/python3.12"),
            runtime_version="3.12.15",
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
        root, python_executable=python_path, runtime_version="3.12.15"
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
        root, python_executable=python_path, runtime_version="3.12.15"
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
        root, python_executable=python_path, runtime_version="3.12.15"
    )
    commands.clear()
    parity = iter((False, True))
    monkeypatch.setattr(
        local_bootstrap,
        "_environment_has_only_locked_packages",
        lambda selected_root, contract, uv: next(parity),
    )

    assert not local_bootstrap.bootstrap_environment(
        root, python_executable=python_path, runtime_version="3.12.15"
    )
    assert [command[1] for command in commands] == ["lock", "sync", "sync", "sync"]


def test_exact_runtime_pin_is_required_before_tool_setup(
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

    with pytest.raises(local_bootstrap.BootstrapError, match="automatic Python downloads"):
        local_bootstrap.bootstrap_environment(
            root,
            python_executable=Path("/usr/bin/python3.12"),
            runtime_version="3.12.3",
        )
    assert not ensure_called


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
    monkeypatch.setattr(
        local_bootstrap, "_environment_has_only_locked_packages", lambda root, contract, uv: True
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
