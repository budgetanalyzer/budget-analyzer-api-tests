from __future__ import annotations

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
) -> None:
    observed: list[list[str]] = []

    def fake_pytest_main(arguments: list[str]) -> int:
        observed.append(arguments)
        return 7

    monkeypatch.setattr("api_tests.local_runner.pytest.main", fake_pytest_main)

    assert local_runner.main(["-m", "auth_smoke"]) == 7
    assert observed == [["--env", "local", "-m", "auth_smoke"]]
