from __future__ import annotations

from collections.abc import Generator

import pytest

from api_tests.auth import AuthConfigurationError, AuthContext, load_auth_context
from api_tests.auth0 import Auth0ManagementError
from api_tests.browser_login import BrowserLoginError
from api_tests.client import GatewayClient
from api_tests.config import EnvironmentConfig, load_environment
from api_tests.run_state import RunState, session_run_state

ENV_CONFIG_KEY: pytest.StashKey[EnvironmentConfig] = pytest.StashKey()


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
