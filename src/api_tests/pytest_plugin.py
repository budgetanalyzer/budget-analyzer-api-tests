from __future__ import annotations

import secrets
from collections.abc import Generator, Iterable
from pathlib import Path
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
from api_tests.coverage import MarkerCounts, OperationKey, build_marker_counts
from api_tests.openapi import OpenApiContractKey, OpenApiDocument, load_snapshot
from api_tests.run_state import RunState, new_run_state
from api_tests.session import (
    IndistinctSessionsError,
    MissingSecondarySessionError,
    Session,
    ensure_distinct_sessions,
)

ENV_CONFIG_KEY: pytest.StashKey[EnvironmentConfig] = pytest.StashKey()
OPENAPI_MARKER_COUNTS_KEY: pytest.StashKey[dict[OperationKey, MarkerCounts]] = pytest.StashKey()


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
        "--request-log",
        action="store",
        default=None,
        help="write redacted public request diagnostics to this JSONL path",
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
    marker_entries: list[tuple[OpenApiContractKey, str, bool]] = []
    for item in items:
        is_placeholder = item.get_closest_marker("placeholder") is not None
        for marker in item.iter_markers("openapi"):
            contract, operation_id = _openapi_marker_operation(marker, item)
            marker_entries.append((contract, operation_id, is_placeholder))
        for marker in item.iter_markers("session_gateway_case"):
            _session_gateway_case_id(marker, item)

        reason = marker_policy_skip_reason(
            env_config,
            (marker.name for marker in item.iter_markers()),
        )
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))
    config.stash[OPENAPI_MARKER_COUNTS_KEY] = dict(build_marker_counts(marker_entries))


def openapi_marker_counts(config: pytest.Config) -> dict[OperationKey, MarkerCounts]:
    return config.stash.get(OPENAPI_MARKER_COUNTS_KEY, {})


def _openapi_marker_operation(
    marker: pytest.Mark,
    item: pytest.Item,
) -> tuple[OpenApiContractKey, str]:
    if len(marker.args) != 1:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.openapi requires exactly one positional operation id"
        )
    operation_id = marker.args[0]
    if not isinstance(operation_id, str) or not operation_id:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.openapi operation id must be a non-empty string"
        )
    if set(marker.kwargs) - {"contract"}:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.openapi accepts only the contract keyword"
        )
    raw_contract = marker.kwargs.get("contract", "unified")
    if raw_contract not in {"unified", "session_gateway"}:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.openapi contract must be 'unified' or 'session_gateway'"
        )
    return cast(OpenApiContractKey, raw_contract), operation_id


def _session_gateway_case_id(marker: pytest.Mark, item: pytest.Item) -> str:
    if len(marker.args) != 1 or marker.kwargs:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.session_gateway_case requires exactly one "
            "positional case id"
        )
    case_id = marker.args[0]
    if not isinstance(case_id, str) or not case_id:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.session_gateway_case case id must be a non-empty string"
        )
    return case_id


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
def session_gateway_openapi_snapshot() -> OpenApiDocument:
    return load_snapshot(repository_root() / "schemas" / "session-gateway-openapi.yaml")


@pytest.fixture(scope="session")
def request_log_path(pytestconfig: pytest.Config) -> Path | None:
    return _request_log_path(pytestconfig)


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
        if env_config.environment_type == "local":
            pytest.fail(f"authorization prerequisite failed: {exc}", pytrace=False)
        pytest.skip(str(exc))
    except (AuthConfigurationError, BrowserLoginError) as exc:
        pytest.fail(f"authentication prerequisite failed: {exc}", pytrace=False)


@pytest.fixture(scope="session")
def distinct_user_sessions(
    primary_session: Session,
    secondary_session: Session,
) -> None:
    try:
        ensure_distinct_sessions(primary_session, secondary_session)
    except IndistinctSessionsError as exc:
        pytest.fail(f"authorization prerequisite failed: {exc}", pytrace=False)


@pytest.fixture(scope="session")
def gateway_client(
    primary_session: Session,
    env_config: EnvironmentConfig,
    request_log_path: Path | None,
) -> Generator[GatewayClient, None, None]:
    with GatewayClient(
        env_config,
        artifact_path=request_log_path,
        session=primary_session,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def secondary_gateway_client(
    secondary_session: Session,
    env_config: EnvironmentConfig,
    request_log_path: Path | None,
) -> Generator[GatewayClient, None, None]:
    with GatewayClient(
        env_config,
        artifact_path=request_log_path,
        session=secondary_session,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def unauthenticated_gateway_client(
    env_config: EnvironmentConfig,
    request_log_path: Path | None,
) -> Generator[GatewayClient, None, None]:
    with GatewayClient(env_config, artifact_path=request_log_path) as client:
        yield client


@pytest.fixture
def invalid_session_gateway_client(
    env_config: EnvironmentConfig,
    request_log_path: Path | None,
) -> Generator[GatewayClient, None, None]:
    invalid_session = Session(
        cookie_name=env_config.session_cookie_name,
        cookie_value=secrets.token_urlsafe(32),
    )
    with GatewayClient(
        env_config,
        artifact_path=request_log_path,
        session=invalid_session,
    ) as client:
        yield client


def _request_log_path(config: pytest.Config) -> Path | None:
    raw_path = config.getoption("--request-log")
    return Path(raw_path) if isinstance(raw_path, str) and raw_path else None
