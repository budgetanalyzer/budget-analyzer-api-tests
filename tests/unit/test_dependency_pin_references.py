from __future__ import annotations

import re
from pathlib import Path

from api_tests.config import repository_root
from api_tests.dependency_contract import validate_contract_files

_VERSIONED_UV_PATH = re.compile(r"uv-\d+\.\d+\.\d+")
_EXCLUDED_DOC_DIRECTORIES = frozenset({"plans", "bugs"})


def _guarded_files(root: Path) -> list[Path]:
    files = [root / "README.md", root / "AGENTS.md", root / "run-tests"]
    files.extend(
        path
        for path in sorted((root / "docs").rglob("*.md"))
        if path.relative_to(root / "docs").parts[0] not in _EXCLUDED_DOC_DIRECTORIES
    )
    files.extend(sorted((root / ".github" / "workflows").glob("*.yml")))
    return files


def pin_reference_violations(root: Path, pins: tuple[str, ...]) -> list[str]:
    literal = re.compile("|".join(rf"(?<![\w.]){re.escape(pin)}(?![\w.]*\d)" for pin in pins))
    violations: list[str] = []
    for path in _guarded_files(root):
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if literal.search(line) or _VERSIONED_UV_PATH.search(line):
                violations.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    return violations


def test_docs_workflows_and_runner_do_not_repeat_tool_pins() -> None:
    root = repository_root()
    contract, issues = validate_contract_files(root)
    assert issues == ()
    assert contract is not None

    violations = pin_reference_violations(
        root, (contract.uv_version, contract.pip_audit_version, contract.python_pin)
    )

    assert violations == [], (
        "Tool pins must appear only in [tool.dependency-contract] in pyproject.toml, "
        "and the exact Python runtime pin only in .python-version. Refer to the pin, "
        "to .python-version, or to .dependency-tools/uv-<pin>/ instead, and run "
        "tools/check-dependency-contract.py without --uv so it resolves the pinned "
        "path:\n" + "\n".join(violations)
    )


def test_guard_reports_reintroduced_literal(tmp_path: Path) -> None:
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "README.md").write_text("ok\nuses uv 9.8.7 here\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("run .dependency-tools/uv-1.2.3/bin/uv\n", encoding="utf-8")
    (tmp_path / "docs" / "plans" / "old.md").write_text("uv 9.8.7\n", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text("version 19.8.70 is fine\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "pip-audit==4.5.6\n", encoding="utf-8"
    )

    violations = pin_reference_violations(tmp_path, ("9.8.7", "4.5.6"))

    assert violations == [
        "README.md:2: uses uv 9.8.7 here",
        "AGENTS.md:1: run .dependency-tools/uv-1.2.3/bin/uv",
        ".github/workflows/ci.yml:1: pip-audit==4.5.6",
    ]


def test_guard_reports_python_patch_pin_but_not_minor_line(tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text(
        "uses the .python-version runtime (currently 3.12.15)\n", encoding="utf-8"
    )
    (tmp_path / "run-tests").write_text("exec python3.12 -m venv\n", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text(
        "supported 3.12 minor line\nrequires >=3.12,<3.13 and ==3.12.*\nnot 3.12.150\n",
        encoding="utf-8",
    )

    violations = pin_reference_violations(tmp_path, ("3.12.15",))

    assert violations == ["README.md:1: uses the .python-version runtime (currently 3.12.15)"]
