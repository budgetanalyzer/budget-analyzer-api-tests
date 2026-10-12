from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from api_tests import dependency_inventory
from api_tests.dependency_contract import DependencyContract, LockedRequirement


def test_inventory_records_runtime_tool_lock_project_and_exact_packages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _root_with_lock(tmp_path)
    commands: list[tuple[str, ...]] = []
    _mock_contract(monkeypatch)

    def fake_run(command: Sequence[str], selected_root: Path) -> subprocess.CompletedProcess[str]:
        commands.append(tuple(command))
        if command[1:] == ("--version",):
            return _result(command, stdout="uv 0.13.0 (test)\n")
        if command[1:3] == ("pip", "list"):
            return _result(
                command,
                stdout=json.dumps(
                    [
                        {"name": "Example", "version": "1.2.3"},
                        {
                            "name": "budget-analyzer-api-tests",
                            "version": "0.1.0",
                            "editable_project_location": str(selected_root),
                        },
                    ]
                ),
            )
        return _result(command)

    monkeypatch.setattr(dependency_inventory, "_run_command", fake_run)
    output = tmp_path / "dependency-inventory.json"

    dependency_inventory.write_dependency_inventory(
        output,
        root=root,
        python_executable=Path("/runtime/python3.12"),
    )

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["python"] == {"implementation": "CPython", "version": "3.12.15"}
    assert document["uv"] == {"version": "0.13.0"}
    assert document["lock"]["path"] == "uv.lock"
    assert len(document["lock"]["sha256"]) == 64
    assert document["project"] == {
        "editable": True,
        "name": "budget-analyzer-api-tests",
        "version": "0.1.0",
    }
    assert document["installed_packages"] == [{"name": "example", "version": "1.2.3"}]
    assert document["parity"]["status"] == "exact"
    assert [command[1] for command in commands] == ["--version", "lock", "sync", "pip"]


def test_inventory_mismatch_fails_without_writing_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _root_with_lock(tmp_path)
    _mock_contract(monkeypatch)

    def fake_run(command: Sequence[str], selected_root: Path) -> subprocess.CompletedProcess[str]:
        del selected_root
        if command[1:] == ("--version",):
            return _result(command, stdout="uv 0.13.0\n")
        if command[1] == "sync":
            return _result(command, returncode=1, stderr="environment is not synchronized")
        return _result(command)

    monkeypatch.setattr(dependency_inventory, "_run_command", fake_run)
    output = tmp_path / "dependency-inventory.json"

    with pytest.raises(
        dependency_inventory.DependencyInventoryError,
        match="does not exactly match",
    ):
        dependency_inventory.write_dependency_inventory(
            output,
            root=root,
            python_executable=Path("/runtime/python3.12"),
        )

    assert not output.exists()


def test_inventory_rejects_package_absent_from_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _root_with_lock(tmp_path)
    _mock_contract(monkeypatch)

    def fake_run(command: Sequence[str], selected_root: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ("--version",):
            return _result(command, stdout="uv 0.13.0\n")
        if command[1:3] == ("pip", "list"):
            return _result(
                command,
                stdout=json.dumps(
                    [
                        {"name": "intruder", "version": "9.9"},
                        {
                            "name": "budget-analyzer-api-tests",
                            "version": "0.1.0",
                            "editable_project_location": str(selected_root),
                        },
                    ]
                ),
            )
        return _result(command)

    monkeypatch.setattr(dependency_inventory, "_run_command", fake_run)

    with pytest.raises(
        dependency_inventory.DependencyInventoryError,
        match=r"absent from uv\.lock",
    ):
        dependency_inventory.write_dependency_inventory(
            tmp_path / "dependency-inventory.json",
            root=root,
            python_executable=Path("/runtime/python3.12"),
        )


def _mock_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = DependencyContract(
        python_pin="3.12.15",
        requires_python=">=3.12,<3.13",
        uv_version="0.13.0",
        pip_audit_version="2.10.1",
        direct_requirements=("example>=1,<2",),
        locked_requirements=(LockedRequirement("example", "1.2.3"),),
    )
    monkeypatch.setattr(
        dependency_inventory,
        "validate_contract_files",
        lambda root: (contract, ()),
    )
    monkeypatch.setattr("api_tests.dependency_inventory.platform.python_version", lambda: "3.12.15")
    monkeypatch.setattr(
        "api_tests.dependency_inventory.platform.python_implementation", lambda: "CPython"
    )
    monkeypatch.setattr(
        dependency_inventory,
        "pinned_uv_executable",
        lambda root, selected_contract: Path("/tools/uv"),
    )


def _root_with_lock(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "uv.lock").write_text("locked graph\n", encoding="utf-8")
    return root


def _result(
    command: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)
