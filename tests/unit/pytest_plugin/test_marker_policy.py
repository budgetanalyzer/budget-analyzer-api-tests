from __future__ import annotations

import pytest

from api_tests import pytest_plugin
from api_tests.config import EnvironmentConfig, load_environment
from api_tests.pytest_plugin import marker_policy_skip_reason
from api_tests.session import Session
from api_tests.session_gateway_assertions import (
    REQUIRED_ADMIN_PERMISSIONS,
    AuthenticatedIdentity,
)


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


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            load_environment("staging"),
            "admin deactivation runs only against the exact local target",
        ),
        (
            load_environment("production"),
            "admin deactivation runs only against the exact local target",
        ),
    ],
    ids=["staging", "production"],
)
def test_admin_deactivation_policy_rejects_non_local_targets(
    config: EnvironmentConfig,
    expected: str,
) -> None:
    assert marker_policy_skip_reason(config, ["admin_deactivation"]) == expected


def test_admin_deactivation_policy_rejects_opaque_local_sessions() -> None:
    config = load_environment("local").model_copy(
        update={"auth": load_environment("production").auth}
    )

    assert marker_policy_skip_reason(config, ["admin_deactivation"]) == (
        "admin deactivation requires browser_preprovisioned mode; opaque supplied sessions "
        "cannot prove disposable ownership"
    )


def test_admin_deactivation_policy_requires_destructive_and_mutation_permissions() -> None:
    config = load_environment("local").model_copy(update={"allow_destructive": False})

    reason = marker_policy_skip_reason(
        config,
        ["admin_deactivation", "admin", "authorization", "mutation", "destructive"],
    )

    assert reason is not None
    assert "disables destructive tests" in reason


def test_admin_deactivation_skip_precedes_network_fixture_setup(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def admin_gateway_client():
            raise AssertionError("network-capable fixture should not run")

        @pytest.mark.admin_deactivation
        @pytest.mark.admin
        @pytest.mark.authorization
        @pytest.mark.mutation
        @pytest.mark.destructive
        def test_deactivation_is_blocked(admin_gateway_client):
            raise AssertionError("test body should not run")
        """
    )

    result = pytester.runpytest("--env", "staging", "-m", "admin_deactivation")

    result.assert_outcomes(skipped=1)


def test_admin_deactivation_marker_requires_all_safety_markers(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.admin_deactivation
        def test_missing_safety_markers():
            pass
        """
    )

    result = pytester.runpytest("--env", "local", "--collect-only")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*admin_deactivation requires markers:*"])


@pytest.mark.parametrize("marker", ["admin", "admin_auth_smoke"])
def test_production_policy_excludes_admin_markers(marker: str) -> None:
    assert marker_policy_skip_reason(load_environment("production"), [marker]) == (
        "production excludes admin scenarios"
    )


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
        import pytest
        from api_tests.client import GatewayClient

        @pytest.mark.authentication
        @pytest.mark.readonly
        @pytest.mark.production_safe
        def test_unauthenticated_client(unauthenticated_gateway_client):
            assert isinstance(unauthenticated_gateway_client, GatewayClient)
        """
    )

    result = pytester.runpytest(
        "--env",
        "production",
        "-m",
        "readonly and production_safe",
    )

    result.assert_outcomes(passed=1)


def test_authentication_marker_does_not_require_a_staging_secondary_session() -> None:
    production = load_environment("production")
    staging_without_secondary = production.model_copy(update={"environment_type": "staging"})

    assert marker_policy_skip_reason(staging_without_secondary, ["authentication"]) is None
    assert marker_policy_skip_reason(staging_without_secondary, ["authorization"]) == (
        "staging authorization tests require a configured secondary supplied session"
    )


def test_production_policy_runs_safe_authentication_reads_and_skips_writes_before_setup(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def unauthenticated_gateway_client(request):
            method = request.node.callspec.params["method"]
            if method != "GET":
                raise AssertionError("unsafe request fixture should not run")
            return object()

        @pytest.mark.parametrize(
            ("method", "path"),
            [
                pytest.param(
                    "GET",
                    "/v1/widgets",
                    marks=[
                        pytest.mark.authentication,
                        pytest.mark.readonly,
                        pytest.mark.production_safe,
                    ],
                ),
                pytest.param(
                    "POST",
                    "/v1/widgets",
                    marks=[pytest.mark.authentication, pytest.mark.mutation],
                ),
            ],
        )
        def test_authentication_case(method, path, unauthenticated_gateway_client):
            assert method == "GET"
            assert path == "/v1/widgets"
            assert unauthenticated_gateway_client is not None
        """
    )

    result = pytester.runpytest("--env", "production")

    result.assert_outcomes(passed=1, skipped=1)


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


def test_admin_only_fixture_acquires_no_ordinary_identity(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    acquired_roles: list[str] = []

    def record_acquisition(config: object, role: str) -> Session:
        del config
        acquired_roles.append(role)
        return Session(cookie_name="BA_SESSION", cookie_value=f"{role}-cookie")

    monkeypatch.setattr(pytest_plugin, "acquire_session", record_acquisition)
    monkeypatch.setattr(
        pytest_plugin,
        "_request_authenticated_identity",
        lambda client, snapshot: AuthenticatedIdentity(
            subject="idp|admin",
            roles=frozenset({"USER", "ADMIN"}),
            permissions=REQUIRED_ADMIN_PERMISSIONS,
        ),
    )
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.admin
        @pytest.mark.admin_auth_smoke
        def test_admin_only(admin_gateway_client):
            assert admin_gateway_client is not None
        """
    )

    result = pytester.runpytest("--env", "local", "-m", "admin_auth_smoke")

    result.assert_outcomes(passed=1)
    assert acquired_roles == ["admin"]


def test_missing_admin_role_exits_with_actionable_code_two(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    monkeypatch.setattr(
        pytest_plugin,
        "acquire_session",
        lambda config, role: Session(cookie_name="BA_SESSION", cookie_value="admin-cookie"),
    )
    monkeypatch.setattr(
        pytest_plugin,
        "_request_authenticated_identity",
        lambda client, snapshot: AuthenticatedIdentity(
            subject="idp|admin-subject",
            roles=frozenset({"USER"}),
            permissions=frozenset(),
        ),
    )
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.admin
        @pytest.mark.admin_auth_smoke
        def test_admin_promotion_stop(admin_gateway_client):
            raise AssertionError("test body must not run")
        """
    )

    result = pytester.runpytest("--env", "local", "-m", "admin_auth_smoke")

    assert result.ret == 2
    result.stdout.fnmatch_lines(
        [
            "*Please promote the test admin user to the ADMIN role in the database.*",
            "*Add ADMIN while retaining USER.*BA_ADMIN_TEST_USERNAME in .env.local.*",
            "*Then rerun the same command.*",
        ]
    )


def test_admin_missing_permissions_fails_without_promotion_message(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    monkeypatch.setattr(
        pytest_plugin,
        "acquire_session",
        lambda config, role: Session(cookie_name="BA_SESSION", cookie_value="admin-cookie"),
    )
    monkeypatch.setattr(
        pytest_plugin,
        "_request_authenticated_identity",
        lambda client, snapshot: AuthenticatedIdentity(
            subject="idp|admin-subject",
            roles=frozenset({"USER", "ADMIN"}),
            permissions=REQUIRED_ADMIN_PERMISSIONS - {"users:write"},
        ),
    )
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.admin
        def test_admin_configuration(admin_gateway_client):
            raise AssertionError("test body must not run")
        """
    )

    result = pytester.runpytest("--env", "local", "-m", "admin")

    result.assert_outcomes(errors=1)
    assert "admin authorization configuration failed" in result.stdout.str()
    assert "users:write" in result.stdout.str()
    assert "Please promote" not in result.stdout.str()


def test_production_admin_skip_happens_before_session_fixture(
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    def unexpected_acquisition(*args: object, **kwargs: object) -> Session:
        del args, kwargs
        raise AssertionError("production must not acquire an admin session")

    monkeypatch.setattr(pytest_plugin, "acquire_session", unexpected_acquisition)
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.admin
        @pytest.mark.admin_auth_smoke
        def test_admin_is_excluded(admin_gateway_client):
            raise AssertionError("test body must not run")
        """
    )

    result = pytester.runpytest("--env", "production", "-m", "admin_auth_smoke", "-rs")

    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*production excludes admin scenarios*"])


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


def test_contract_qualified_openapi_marker_collects(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.openapi("logout", contract="session_gateway")
        def test_qualified_marker():
            pass
        """
    )

    result = pytester.runpytest("--env", "local", "--collect-only")

    assert result.ret == pytest.ExitCode.OK


def test_session_gateway_case_marker_collects(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.session_gateway_case("current-user-authenticated")
        def test_case_marker():
            pass
        """
    )

    result = pytester.runpytest("--env", "local", "--collect-only")

    assert result.ret == pytest.ExitCode.OK


def test_forbidden_response_marker_collects(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.forbidden_response("getUsers")
        def test_forbidden_response_marker():
            pass
        """
    )

    result = pytester.runpytest("--env", "local", "--collect-only")

    assert result.ret == pytest.ExitCode.OK


@pytest.mark.parametrize(
    "marker",
    [
        "@pytest.mark.forbidden_response()",
        '@pytest.mark.forbidden_response("getUsers", contract="unified")',
        "@pytest.mark.forbidden_response(7)",
        '@pytest.mark.forbidden_response("")',
    ],
)
def test_invalid_forbidden_response_marker_fails_collection(
    pytester: pytest.Pytester,
    marker: str,
) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        f"""
        import pytest

        {marker}
        def test_invalid_marker():
            pass
        """
    )

    result = pytester.runpytest("--env", "local", "--collect-only")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*pytest.mark.forbidden_response*"])


@pytest.mark.parametrize(
    "marker",
    [
        "@pytest.mark.session_gateway_case()",
        '@pytest.mark.session_gateway_case("case", extra=True)',
        "@pytest.mark.session_gateway_case(7)",
    ],
)
def test_invalid_session_gateway_case_marker_fails_collection(
    pytester: pytest.Pytester,
    marker: str,
) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        f"""
        import pytest

        {marker}
        def test_invalid_marker():
            pass
        """
    )

    result = pytester.runpytest("--env", "local", "--collect-only")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*pytest.mark.session_gateway_case*"])


@pytest.mark.parametrize(
    "marker",
    [
        '@pytest.mark.openapi("logout", namespace="session_gateway")',
        '@pytest.mark.openapi("logout", contract="unknown")',
    ],
)
def test_invalid_openapi_contract_marker_fails_collection(
    pytester: pytest.Pytester,
    marker: str,
) -> None:
    pytester.makeconftest('pytest_plugins = ["api_tests.pytest_plugin"]')
    pytester.makepyfile(
        f"""
        import pytest

        {marker}
        def test_invalid_marker():
            pass
        """
    )

    result = pytester.runpytest("--env", "local", "--collect-only")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*pytest.mark.openapi*"])
