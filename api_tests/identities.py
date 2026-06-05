from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from api_tests.auth0 import Auth0CreatedUser, Auth0ManagementClient, settings_from_environment
from api_tests.browser_login import BrowserLoginCredentials, SessionCookie, login_with_browser
from api_tests.config import EnvironmentConfig
from api_tests.run_state import RunState


@dataclass(frozen=True, repr=False)
class RunUser:
    label: str
    email: str
    auth0_user_id: str
    password: str = field(repr=False)
    session_cookie: SessionCookie = field(repr=False)


@dataclass(frozen=True)
class RunIdentities:
    primary_user: RunUser
    secondary_user: RunUser


LoginFunction = Callable[[EnvironmentConfig, BrowserLoginCredentials], SessionCookie]


def create_run_identities(
    config: EnvironmentConfig,
    run_state: RunState,
    *,
    auth0_client: Auth0ManagementClient | None = None,
    login: LoginFunction = login_with_browser,
) -> RunIdentities:
    if auth0_client is not None:
        return _create_run_identities_with_client(config, run_state, auth0_client, login)

    settings = settings_from_environment(config)
    with Auth0ManagementClient(
        settings,
        timeout_seconds=config.timeouts.request_seconds,
    ) as managed_client:
        return _create_run_identities_with_client(config, run_state, managed_client, login)


def _create_run_identities_with_client(
    config: EnvironmentConfig,
    run_state: RunState,
    auth0_client: Auth0ManagementClient,
    login: LoginFunction,
) -> RunIdentities:
    primary_user = _create_logged_in_user(
        label="primary-user",
        config=config,
        run_state=run_state,
        auth0_client=auth0_client,
        login=login,
    )
    secondary_user = _create_logged_in_user(
        label="secondary-user",
        config=config,
        run_state=run_state,
        auth0_client=auth0_client,
        login=login,
    )

    return RunIdentities(primary_user=primary_user, secondary_user=secondary_user)


def _create_logged_in_user(
    *,
    label: str,
    config: EnvironmentConfig,
    run_state: RunState,
    auth0_client: Auth0ManagementClient,
    login: LoginFunction,
) -> RunUser:
    created_user = auth0_client.create_database_user(run_state=run_state, label=label)
    session_cookie = login(
        config,
        BrowserLoginCredentials(email=created_user.email, password=created_user.password),
    )
    return run_user_from_auth0_user(label=label, created_user=created_user, cookie=session_cookie)


def run_user_from_auth0_user(
    *,
    label: str,
    created_user: Auth0CreatedUser,
    cookie: SessionCookie,
) -> RunUser:
    return RunUser(
        label=label,
        email=created_user.email,
        password=created_user.password,
        auth0_user_id=created_user.user_id,
        session_cookie=cookie,
    )
