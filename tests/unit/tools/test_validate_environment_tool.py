from __future__ import annotations

import pytest

from api_tests.tools.validate_environment import main


def test_validate_environment_tool_defaults_to_local(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "name: local" in captured.out
    assert "client-secret" not in captured.out
    assert captured.err == ""


def test_validate_environment_tool_reports_missing_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--env", "does-not-exist"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "environment validation failed: environment file not found:" in captured.err
    assert "does-not-exist.yaml" in captured.err


def test_validate_environment_tool_reports_invalid_environment_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--env", "../local"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "environment validation failed: invalid environment name: ../local" in captured.err
