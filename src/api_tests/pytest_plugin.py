from __future__ import annotations

from collections.abc import Generator, Iterable
from typing import cast

import pytest

from api_tests.auth import AuthConfigurationError, acquire_session
from api_tests.browser_login import BrowserLoginError
from api_tests.client import GatewayClient
from api_tests.config import (
    AuthMode,
    EnvironmentConfig,
    load_environment,
    repository_root,
    with_session_mode,
)
from api_tests.coverage import MarkerCounts, build_marker_counts
from api_tests.openapi import OpenApiDocument, load_snapshot
from api_tests.run_state import RunState, new_run_state
from api_tests.session import MissingSecondarySessionError, Session

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
    group.addoption(
        "--session-mode",
        action="store",
        choices=("supplied_sessions", "browser_preprovisioned"),
        default=None,
        help="override auth.mode from the environment file for this pytest run",
    )


def pytest_configure(config: pytest.Config) -> None:
    env_name = str(config.getoption("--env"))
    raw_session_mode = config.getoption("--session-mode")
    session_mode = cast(AuthMode | None, raw_session_mode)
    try:
        config.stash[ENV_CONFIG_KEY] = with_session_mode(load_environment(env_name), session_mode)
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
    if (
        env_config.environment_type == "staging"
        and "authorization" in names
        and env_config.auth.mode == "supplied_sessions"
        and env_config.auth.supplied_sessions.secondary_session_env is None
    ):
        return "staging authorization tests require a configured secondary supplied session"
    return None


@pytest.fixture(scope="session")
def env_config(pytestconfig: pytest.Config) -> EnvironmentConfig:
    return pytestconfig.stash[ENV_CONFIG_KEY]


@pytest.fixture(scope="session")
def run_state() -> RunState:
    return new_run_state()


@pytest.fixture(scope="session")
def openapi_snapshot() -> OpenApiDocument:
    return load_snapshot(repository_root() / "schemas" / "openapi.json")


@pytest.fixture(scope="session")
def primary_session(env_config: EnvironmentConfig) -> Session:
    try:
        return acquire_session(env_config, "primary")
    except (AuthConfigurationError, BrowserLoginError) as exc:
        pytest.fail(f"authentication prerequisite failed: {exc}", pytrace=False)


@pytest.fixture(scope="session")
def secondary_session(env_config: EnvironmentConfig) -> Session:
    try:
        return acquire_session(env_config, "secondary")
    except MissingSecondarySessionError as exc:
        pytest.skip(str(exc))
    except (AuthConfigurationError, BrowserLoginError) as exc:
        pytest.fail(f"authentication prerequisite failed: {exc}", pytrace=False)


@pytest.fixture(scope="session")
def gateway_client(
    primary_session: Session,
    env_config: EnvironmentConfig,
) -> Generator[GatewayClient, None, None]:
    with GatewayClient(env_config, session=primary_session) as client:
        yield client


@pytest.fixture(scope="session")
def secondary_gateway_client(
    secondary_session: Session,
    env_config: EnvironmentConfig,
) -> Generator[GatewayClient, None, None]:
    with GatewayClient(env_config, session=secondary_session) as client:
        yield client


@pytest.fixture(scope="session")
def unauthenticated_gateway_client(
    env_config: EnvironmentConfig,
) -> Generator[GatewayClient, None, None]:
    with GatewayClient(env_config) as client:
        yield client
