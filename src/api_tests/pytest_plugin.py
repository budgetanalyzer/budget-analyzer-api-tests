from __future__ import annotations

from collections.abc import Generator, Iterable
from typing import cast

import pytest
from _pytest.terminal import TerminalReporter

from api_tests.auth import AuthConfigurationError, load_session_context
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
from api_tests.identities import PreprovisionedSessionError
from api_tests.openapi import OpenApiDocument, load_snapshot
from api_tests.prerequisites import PrerequisiteScope, run_live_preflight
from api_tests.run_state import RunState, session_run_state
from api_tests.session import (
    MissingSecondarySessionError,
    SessionBundle,
    SessionContext,
    secondary_only_bundle,
)

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
    group.addoption(
        "--require-local-target",
        action="store_true",
        help=(
            "require the resolved live target to be the exact supported local HTTPS origin; "
            "does not change TLS verification"
        ),
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
        auth_smoke_reason = _auth_smoke_skip_reason(config, item)
        if auth_smoke_reason is not None:
            item.add_marker(pytest.mark.skip(reason=auth_smoke_reason))

    config.stash[OPENAPI_MARKER_COUNTS_KEY] = dict(build_marker_counts(marker_entries))


def pytest_collection_finish(session: pytest.Session) -> None:
    if session.config.option.collectonly or session.testsfailed:
        return

    scope = selected_live_scope(session.items)
    if scope is None:
        return

    environment_name = str(session.config.getoption("--env"))
    result = run_live_preflight(
        session.config.stash[ENV_CONFIG_KEY],
        environment_name=environment_name,
        scope=scope,
        require_local_target=bool(session.config.getoption("--require-local-target")),
    )
    if not result.succeeded:
        pytest.exit(
            f"live prerequisite failed [{result.category.value}]: {result.message}",
            returncode=int(result.exit_code),
        )
    terminal_reporter = cast(
        TerminalReporter | None,
        session.config.pluginmanager.get_plugin("terminalreporter"),
    )
    if terminal_reporter is not None:
        terminal_reporter.write_line(
            f"live prerequisites passed [{result.category.value}]: {result.message}"
        )


def selected_live_scope(items: Iterable[pytest.Item]) -> PrerequisiteScope | None:
    selected_scope: PrerequisiteScope | None = None
    for item in items:
        if item.get_closest_marker("skip") is not None:
            continue
        fixture_names = frozenset(cast(Iterable[str], getattr(item, "fixturenames", ())))
        if {"secondary_gateway_client", "secondary_session_bundle"} & fixture_names:
            return PrerequisiteScope.AUTHORIZATION
        if {"gateway_client", "session_context", "session_bundle"} & fixture_names:
            selected_scope = PrerequisiteScope.AUTHENTICATED
        elif "unauthenticated_gateway_client" in fixture_names and selected_scope is None:
            selected_scope = PrerequisiteScope.PUBLIC
    return selected_scope


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


def _auth_smoke_skip_reason(config: pytest.Config, item: pytest.Item) -> str | None:
    if item.get_closest_marker("auth_smoke") is None:
        return None
    mark_expression = str(config.getoption("markexpr") or "")
    if "auth_smoke" in mark_expression:
        return None
    return "auth_smoke tests run only when selected with -m auth_smoke"


@pytest.fixture(scope="session")
def env_config(pytestconfig: pytest.Config) -> EnvironmentConfig:
    return pytestconfig.stash[ENV_CONFIG_KEY]


@pytest.fixture(scope="session")
def run_state() -> RunState:
    return session_run_state()


@pytest.fixture(scope="session")
def openapi_snapshot() -> OpenApiDocument:
    return load_snapshot(repository_root() / "schemas" / "openapi.json")


@pytest.fixture(scope="session")
def session_context(env_config: EnvironmentConfig, run_state: RunState) -> SessionContext:
    try:
        return load_session_context(env_config, run_state)
    except (AuthConfigurationError, BrowserLoginError, PreprovisionedSessionError) as exc:
        pytest.fail(f"authentication prerequisite failed: {exc}", pytrace=False)


@pytest.fixture(scope="session")
def session_bundle(session_context: SessionContext) -> SessionBundle:
    return session_context.bundle


@pytest.fixture(scope="session")
def secondary_session_bundle(session_bundle: SessionBundle) -> SessionBundle:
    try:
        return secondary_only_bundle(session_bundle)
    except MissingSecondarySessionError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def gateway_client(
    session_bundle: SessionBundle,
    env_config: EnvironmentConfig,
) -> Generator[GatewayClient, None, None]:
    with GatewayClient(env_config, session_bundle=session_bundle) as client:
        yield client


@pytest.fixture(scope="session")
def secondary_gateway_client(
    secondary_session_bundle: SessionBundle,
    env_config: EnvironmentConfig,
) -> Generator[GatewayClient, None, None]:
    with GatewayClient(env_config, session_bundle=secondary_session_bundle) as client:
        yield client


@pytest.fixture(scope="session")
def unauthenticated_gateway_client(
    env_config: EnvironmentConfig,
) -> Generator[GatewayClient, None, None]:
    with GatewayClient(env_config) as client:
        yield client
