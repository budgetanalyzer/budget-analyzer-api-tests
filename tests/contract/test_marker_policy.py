from __future__ import annotations

import pytest

from api_tests import pytest_plugin
from api_tests.config import load_environment
from api_tests.prerequisites import (
    PrerequisiteCategory,
    PrerequisiteExitCode,
    PrerequisiteResult,
    PrerequisiteScope,
)
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


def test_unauthenticated_gateway_client_does_not_acquire_sessions(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    monkeypatch.setattr(pytest_plugin, "run_live_preflight", _successful_preflight)
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


def test_secondary_gateway_client_reports_missing_secondary_prerequisite(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    monkeypatch.setenv("BA_PRIMARY_SESSION", "primary-cookie")
    monkeypatch.setattr(pytest_plugin, "run_live_preflight", _successful_preflight)
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


def test_live_preflight_runs_once_at_highest_selected_fixture_scope(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    scopes: list[PrerequisiteScope] = []

    def record_preflight(*args: object, **kwargs: object) -> PrerequisiteResult:
        del args
        scope = kwargs["scope"]
        assert isinstance(scope, PrerequisiteScope)
        assert kwargs["require_local_target"] is True
        scopes.append(scope)
        return _successful_preflight()

    monkeypatch.setattr(pytest_plugin, "run_live_preflight", record_preflight)
    pytester.makeconftest(
        """
        pytest_plugins = ["api_tests.pytest_plugin"]

        import pytest

        @pytest.fixture
        def gateway_client():
            return object()

        @pytest.fixture
        def secondary_session_bundle():
            return object()
        """
    )
    pytester.makepyfile(
        """
        def test_authenticated(gateway_client):
            pass

        def test_authorization(secondary_session_bundle):
            pass
        """
    )

    result = pytester.runpytest("--env", "local", "--require-local-target")

    result.assert_outcomes(passed=2)
    assert scopes == [PrerequisiteScope.AUTHORIZATION]


def test_collect_only_does_not_run_preflight(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    calls: list[object] = []

    def record_preflight(*args: object, **kwargs: object) -> PrerequisiteResult:
        calls.append((args, kwargs))
        return _successful_preflight()

    monkeypatch.setattr(pytest_plugin, "run_live_preflight", record_preflight)
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        def test_offline(env_config):
            assert env_config.name == "local"

        def test_live_but_collection_only(unauthenticated_gateway_client):
            pass
        """
    )

    collection = pytester.runpytest("--env", "local", "--collect-only")
    assert collection.ret == pytest.ExitCode.OK
    assert calls == []


def test_offline_tests_do_not_run_preflight(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    calls: list[object] = []

    def record_preflight(*args: object, **kwargs: object) -> PrerequisiteResult:
        calls.append((args, kwargs))
        return _successful_preflight()

    monkeypatch.setattr(pytest_plugin, "run_live_preflight", record_preflight)
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        def test_offline(env_config):
            assert env_config.name == "local"
        """
    )

    result = pytester.runpytest("--env", "local")

    result.assert_outcomes(passed=1)
    assert calls == []


def _successful_preflight(*args: object, **kwargs: object) -> PrerequisiteResult:
    del args, kwargs
    return PrerequisiteResult(
        category=PrerequisiteCategory.READY,
        exit_code=PrerequisiteExitCode.OK,
        message="ready",
    )
