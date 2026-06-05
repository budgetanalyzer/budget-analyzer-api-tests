from __future__ import annotations

import pytest

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
