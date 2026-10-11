from __future__ import annotations

import pytest

from api_tests import pytest_plugin
from api_tests.config import load_environment
from api_tests.pytest_plugin import marker_policy_skip_reason
from api_tests.session import Session


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


def test_unauthenticated_gateway_client_does_not_acquire_sessions(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    def unexpected_acquisition(*args: object, **kwargs: object) -> Session:
        del args, kwargs
        raise AssertionError("session acquisition should not run")

    monkeypatch.setattr(pytest_plugin, "acquire_session", unexpected_acquisition)
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        from api_tests.client import GatewayClient

        def test_unauthenticated_client(unauthenticated_gateway_client):
            assert isinstance(unauthenticated_gateway_client, GatewayClient)
        """
    )

    result = pytester.runpytest("--env", "production")

    result.assert_outcomes(passed=1)


def test_primary_fixture_does_not_eagerly_acquire_secondary_session(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    acquired_roles: list[str] = []

    def record_acquisition(config: object, role: str) -> Session:
        del config
        acquired_roles.append(role)
        return Session(cookie_name="BA_SESSION", cookie_value=f"{role}-cookie")

    monkeypatch.setattr(pytest_plugin, "acquire_session", record_acquisition)
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        from api_tests.client import GatewayClient

        def test_primary_client(gateway_client):
            assert isinstance(gateway_client, GatewayClient)
        """
    )

    result = pytester.runpytest("--env", "local")

    result.assert_outcomes(passed=1)
    assert acquired_roles == ["primary"]


def test_auth_smoke_is_not_silently_skipped_without_marker_expression(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.auth_smoke
        def test_auth_smoke_outcome_is_visible():
            pass
        """
    )

    result = pytester.runpytest("--env", "local")

    result.assert_outcomes(passed=1)


def test_secondary_gateway_client_reports_missing_secondary_session(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    monkeypatch.setenv("BA_PRIMARY_SESSION", "primary-cookie")
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.authorization
        @pytest.mark.readonly
        def test_secondary_client(secondary_gateway_client):
            raise AssertionError("test body should be skipped")
        """
    )

    result = pytester.runpytest("--env", "production", "-m", "authorization", "-rs")

    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*secondary session prerequisite failed:*"])


def test_session_mode_override_can_select_supplied_sessions(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        def test_session_mode(env_config):
            assert env_config.auth.mode == "supplied_sessions"
        """
    )

    result = pytester.runpytest("--env", "local", "--session-mode", "supplied_sessions")

    result.assert_outcomes(passed=1)


def test_session_mode_override_cannot_weaken_production_policy(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile("def test_unreachable(): pass")

    result = pytester.runpytest(
        "--env",
        "production",
        "--session-mode",
        "browser_preprovisioned",
    )

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        [
            "*failed to load --env 'production':*",
            "*production auth.mode must use supplied_sessions*",
        ]
    )


def test_collection_does_not_acquire_sessions_or_construct_clients(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    def unexpected_action(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("collection should not perform setup")

    monkeypatch.setattr(pytest_plugin, "acquire_session", unexpected_action)
    monkeypatch.setattr(pytest_plugin, "GatewayClient", unexpected_action)
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        def test_live_but_collection_only(gateway_client):
            pass
        """
    )

    result = pytester.runpytest("--env", "local", "--collect-only")

    assert result.ret == pytest.ExitCode.OK
