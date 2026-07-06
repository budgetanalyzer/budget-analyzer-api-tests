from __future__ import annotations

import pytest

from api_tests.config import load_environment
from api_tests.pytest_plugin import marker_policy_skip_reason


def test_mutation_marker_is_blocked_when_environment_disables_mutation() -> None:
    config = load_environment("production")

    assert (
        marker_policy_skip_reason(config, ["mutation"]) == f"{config.name} disables mutation tests"
    )


def test_destructive_marker_is_blocked_when_environment_disables_destructive() -> None:
    config = load_environment("production")
    reason = marker_policy_skip_reason(config, ["destructive", "mutation"])

    assert reason is not None
    assert f"{config.name} disables destructive tests" in reason


def test_mutation_and_destructive_tests_skip_before_request_fixture_setup(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def gateway_client():
            raise AssertionError("gateway_client fixture should not run")

        @pytest.mark.mutation
        def test_mutation_is_blocked(gateway_client):
            raise AssertionError("test body should not run")

        @pytest.mark.destructive
        def test_destructive_is_blocked(gateway_client):
            raise AssertionError("test body should not run")
        """
    )

    result = pytester.runpytest("--env", "production", "-m", "mutation or destructive")

    result.assert_outcomes(skipped=2)
