from __future__ import annotations

from pathlib import Path

import pytest

from api_tests import local_runner


@pytest.mark.parametrize("argument", ["--env", "--env=production", "--env=local"])
def test_runner_rejects_environment_options(
    argument: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert local_runner.main([argument]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "always uses --env local" in captured.err


def test_runner_passes_pytest_selectors_after_fixed_local_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: list[list[str]] = []

    def fake_pytest_main(arguments: list[str]) -> int:
        observed.append(arguments)
        return 7

    monkeypatch.setattr("api_tests.local_runner.pytest.main", fake_pytest_main)
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    monkeypatch.setattr(local_runner, "_new_artifact_dir", lambda parent: artifact_dir)

    assert local_runner.main(["-m", "auth_smoke"]) == 7
    assert observed == [
        [
            "-p",
            "api_tests.pytest_plugin",
            "--env",
            "local",
            "--request-log",
            str(artifact_dir / "request-log.jsonl"),
            "--junitxml",
            str(artifact_dir / "pytest.xml"),
            "-m",
            "auth_smoke",
        ]
    ]
    assert "PARTIAL VALIDATION" in capsys.readouterr().out


def test_runner_without_arguments_uses_full_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    observed: list[tuple[object, Path]] = []

    monkeypatch.setattr(local_runner, "_new_artifact_dir", lambda parent: artifact_dir)

    def fake_full_validation(config: object, path: Path) -> int:
        observed.append((config, path))
        return 9

    monkeypatch.setattr(local_runner, "_run_full_validation", fake_full_validation)

    assert local_runner.main([]) == 9
    assert observed[0][1] == artifact_dir
