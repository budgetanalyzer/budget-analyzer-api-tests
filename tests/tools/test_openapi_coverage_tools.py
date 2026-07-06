from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_tests.tools.openapi_coverage import check_main, export_main


def test_export_openapi_coverage_writes_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "openapi-coverage.json"

    exit_code = export_main(["--env", "local", "--output", str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "wrote OpenAPI coverage artifact:" in captured.out

    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(rows, list)
    assert rows
    assert {
        "operation_id",
        "method",
        "path",
        "source",
        "deferred_admin",
        "marker_count",
        "placeholder_marker_count",
        "status",
    }.issubset(rows[0])


def test_check_openapi_coverage_fails_placeholder_gate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "openapi-coverage.json"

    exit_code = check_main(["--env", "local", "--output", str(output_path), "--fail-placeholder"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "placeholder OpenAPI coverage remains:" in captured.err


def test_check_openapi_coverage_fails_missing_for_invalid_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = check_main(["--env", "does-not-exist", "--fail-missing"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "OpenAPI coverage failed: environment file not found:" in captured.err
