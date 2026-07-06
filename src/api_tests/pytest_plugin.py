from __future__ import annotations

from collections.abc import Generator, Iterable

import pytest

from api_tests.auth import AuthConfigurationError, AuthContext, load_auth_context
from api_tests.auth0 import Auth0ManagementError
from api_tests.browser_login import BrowserLoginError
from api_tests.client import GatewayClient
from api_tests.config import EnvironmentConfig, load_environment
from api_tests.coverage import MarkerCounts, build_marker_counts
from api_tests.run_state import RunState, session_run_state

ENV_CONFIG_KEY: pytest.StashKey[EnvironmentConfig] = pytest.StashKey()
OPENAPI_MARKER_COUNTS_KEY: pytest.StashKey[dict[str, MarkerCounts]] = pytest.StashKey()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("budget-analyzer-api-tests")
    group.addoption(
        "--env",
        action="store",
        default="local",
        help="environment name to load from environments/<env>.yaml",
    )


def pytest_configure(config: pytest.Config) -> None:
    env_name = str(config.getoption("--env"))
    try:
        config.stash[ENV_CONFIG_KEY] = load_environment(env_name)
    except Exception as exc:
        raise pytest.UsageError(f"failed to load --env {env_name!r}: {exc}") from exc


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    env_config = config.stash[ENV_CONFIG_KEY]
    marker_entries: list[tuple[str, bool]] = []
    for item in items:
        is_placeholder = item.get_closest_marker("placeholder") is not None
        for marker in item.iter_markers("openapi"):
            marker_entries.append((_openapi_marker_operation_id(marker, item), is_placeholder))

        reason = marker_policy_skip_reason(
            env_config,
            (marker.name for marker in item.iter_markers()),
        )
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))

    config.stash[OPENAPI_MARKER_COUNTS_KEY] = dict(build_marker_counts(marker_entries))


def openapi_marker_counts(config: pytest.Config) -> dict[str, MarkerCounts]:
    return config.stash.get(OPENAPI_MARKER_COUNTS_KEY, {})


def _openapi_marker_operation_id(marker: pytest.Mark, item: pytest.Item) -> str:
    if len(marker.args) != 1 or marker.kwargs:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.openapi requires exactly one positional operation id"
        )
    operation_id = marker.args[0]
    if not isinstance(operation_id, str) or not operation_id:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.openapi operation id must be a non-empty string"
        )
    return operation_id


def marker_policy_skip_reason(
    env_config: EnvironmentConfig,
    marker_names: Iterable[str],
) -> str | None:
    names = frozenset(marker_names)
    if "destructive" in names and not env_config.allow_destructive:
        return (
            f"{env_config.name} disables destructive tests; set allow_destructive only for "
            "non-production environments with an explicit data safety plan"
        )
    if "mutation" in names and not env_config.allow_mutation:
        return f"{env_config.name} disables mutation tests"
    return None


@pytest.fixture(scope="session")
def env_config(pytestconfig: pytest.Config) -> EnvironmentConfig:
    return pytestconfig.stash[ENV_CONFIG_KEY]


@pytest.fixture(scope="session")
def run_state() -> RunState:
    return session_run_state()


@pytest.fixture(scope="session")
def auth_context(env_config: EnvironmentConfig, run_state: RunState) -> AuthContext:
    try:
        return load_auth_context(env_config, run_state)
    except (AuthConfigurationError, Auth0ManagementError, BrowserLoginError) as exc:
        pytest.fail(f"authentication prerequisite failed: {exc}", pytrace=False)


@pytest.fixture(scope="session")
def gateway_client(
    auth_context: AuthContext,
    env_config: EnvironmentConfig,
) -> Generator[GatewayClient, None, None]:
    with GatewayClient(env_config, session_cookie=auth_context.session_cookie) as client:
        yield client
