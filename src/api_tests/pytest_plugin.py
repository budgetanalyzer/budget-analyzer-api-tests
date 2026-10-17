from __future__ import annotations

import secrets
from collections.abc import Generator, Iterable
from pathlib import Path
from typing import cast

import pytest

from api_tests.auth import AuthConfigurationError, acquire_session, admin_promotion_message
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
from api_tests.local_credentials import LOCAL_ENVIRONMENT_NAME, LOCAL_ORIGIN
from api_tests.openapi import OpenApiContractKey, OpenApiDocument, load_snapshot
from api_tests.run_state import RunState, new_run_state
from api_tests.session import (
    IndistinctSessionsError,
    MissingAdminSessionError,
    MissingSecondarySessionError,
    Session,
    ensure_distinct_sessions,
)
from api_tests.session_gateway_assertions import (
    AdminPromotionRequired,
    AuthenticatedIdentity,
    AuthorizationConfigurationError,
    IndistinctIdentityError,
    authenticated_identity,
    ensure_distinct_identities,
    require_admin_identity,
    require_normal_identity,
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
        for marker in item.iter_markers("forbidden_response"):
            _forbidden_response_marker_operation(marker, item)
        for marker in item.iter_markers("session_gateway_case"):
            _session_gateway_case_id(marker, item)

        _validate_admin_deactivation_markers(item)

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


def _forbidden_response_marker_operation(marker: pytest.Mark, item: pytest.Item) -> str:
    if len(marker.args) != 1 or marker.kwargs:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.forbidden_response requires exactly one "
            "positional operation id"
        )
    operation_id = marker.args[0]
    if not isinstance(operation_id, str) or not operation_id:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.forbidden_response operation id must be a "
            "non-empty string"
        )
    return operation_id


def marker_policy_skip_reason(
    env_config: EnvironmentConfig,
    marker_names: Iterable[str],
) -> str | None:
    names = frozenset(marker_names)
    if "admin_deactivation" in names:
        if (
            env_config.name != LOCAL_ENVIRONMENT_NAME
            or env_config.environment_type != "local"
            or env_config.origin != LOCAL_ORIGIN
        ):
            return "admin deactivation runs only against the exact local target"
        if env_config.auth.mode != "browser_preprovisioned":
            return (
                "admin deactivation requires browser_preprovisioned mode; opaque supplied "
                "sessions cannot prove disposable ownership"
            )
    if "destructive" in names and not env_config.allow_destructive:
        return (
            f"{env_config.name} disables destructive tests; set allow_destructive only for "
            "non-production environments with an explicit data safety plan"
        )
    if "mutation" in names and not env_config.allow_mutation:
        return f"{env_config.name} disables mutation tests"
    if env_config.environment_type == "production" and names & {"admin", "admin_auth_smoke"}:
        return "production excludes admin scenarios"
    if (
        env_config.environment_type == "staging"
        and "authorization" in names
        and env_config.auth.mode == "supplied_sessions"
        and env_config.auth.supplied_sessions.secondary_session_env is None
    ):
        return "staging authorization tests require a configured secondary supplied session"
    return None


def _validate_admin_deactivation_markers(item: pytest.Item) -> None:
    names = {marker.name for marker in item.iter_markers()}
    if "admin_deactivation" not in names:
        return
    required = {"admin", "authorization", "mutation", "destructive"}
    missing = sorted(required - names)
    if missing:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.admin_deactivation requires markers: "
            + ", ".join(missing)
        )


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
def admin_session(env_config: EnvironmentConfig) -> Session:
    if env_config.environment_type == "production":
        pytest.skip("production excludes admin session acquisition")
    try:
        return acquire_session(env_config, "admin")
    except MissingAdminSessionError as exc:
        pytest.fail(f"admin authentication prerequisite failed: {exc}", pytrace=False)
    except (AuthConfigurationError, BrowserLoginError) as exc:
        pytest.fail(f"admin authentication prerequisite failed: {exc}", pytrace=False)


@pytest.fixture(scope="session")
def primary_identity(
    gateway_client: GatewayClient,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> AuthenticatedIdentity:
    identity = _request_authenticated_identity(gateway_client, session_gateway_openapi_snapshot)
    try:
        require_normal_identity(identity, label="primary")
    except AuthorizationConfigurationError as exc:
        pytest.fail(f"authorization prerequisite failed: {exc}", pytrace=False)
    return identity


@pytest.fixture(scope="session")
def secondary_identity(
    secondary_gateway_client: GatewayClient,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> AuthenticatedIdentity:
    identity = _request_authenticated_identity(
        secondary_gateway_client, session_gateway_openapi_snapshot
    )
    try:
        require_normal_identity(identity, label="secondary")
    except AuthorizationConfigurationError as exc:
        pytest.fail(f"authorization prerequisite failed: {exc}", pytrace=False)
    return identity


@pytest.fixture(scope="session")
def admin_identity(
    admin_session: Session,
    env_config: EnvironmentConfig,
    request_log_path: Path | None,
    session_gateway_openapi_snapshot: OpenApiDocument,
) -> AuthenticatedIdentity:
    with GatewayClient(
        env_config,
        artifact_path=request_log_path,
        session=admin_session,
    ) as client:
        identity = _request_authenticated_identity(client, session_gateway_openapi_snapshot)
    try:
        require_admin_identity(identity)
    except AdminPromotionRequired as exc:
        pytest.exit(admin_promotion_message(env_config, exc.subject), returncode=2)
    except AuthorizationConfigurationError as exc:
        pytest.fail(f"admin authorization configuration failed: {exc}", pytrace=False)
    return identity


@pytest.fixture(scope="session")
def distinct_user_sessions(
    primary_session: Session,
    secondary_session: Session,
    primary_identity: AuthenticatedIdentity,
    secondary_identity: AuthenticatedIdentity,
) -> None:
    try:
        ensure_distinct_sessions(primary_session, secondary_session)
    except IndistinctSessionsError as exc:
        pytest.fail(f"authorization prerequisite failed: {exc}", pytrace=False)
    try:
        ensure_distinct_identities(
            (("primary", primary_identity), ("secondary", secondary_identity))
        )
    except IndistinctIdentityError as exc:
        pytest.fail(f"authorization prerequisite failed: {exc}", pytrace=False)


@pytest.fixture(scope="session")
def distinct_admin_user_identities(
    primary_identity: AuthenticatedIdentity,
    secondary_identity: AuthenticatedIdentity,
    admin_identity: AuthenticatedIdentity,
) -> None:
    try:
        ensure_distinct_identities(
            (
                ("primary", primary_identity),
                ("secondary", secondary_identity),
                ("admin", admin_identity),
            )
        )
    except IndistinctIdentityError as exc:
        pytest.fail(f"admin authorization prerequisite failed: {exc}", pytrace=False)


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
def admin_gateway_client(
    admin_identity: AuthenticatedIdentity,
    admin_session: Session,
    env_config: EnvironmentConfig,
    request_log_path: Path | None,
) -> Generator[GatewayClient, None, None]:
    del admin_identity
    with GatewayClient(
        env_config,
        artifact_path=request_log_path,
        session=admin_session,
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


def _request_authenticated_identity(
    client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> AuthenticatedIdentity:
    response = client.raw_request("GET", "/auth/v1/user")
    return authenticated_identity(response, openapi_snapshot)
