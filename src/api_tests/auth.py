from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from api_tests.auth0 import Auth0ManagementClient, settings_from_environment
from api_tests.browser_login import SessionCookie
from api_tests.config import EnvironmentConfig
from api_tests.identities import RunIdentities, create_run_identities
from api_tests.run_state import RunState


class AuthConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class AuthContext:
    mode: str
    session_cookie: SessionCookie
    identities: RunIdentities | None = None


def load_auth_context(
    config: EnvironmentConfig,
    run_state: RunState,
    *,
    environ: Mapping[str, str] | None = None,
) -> AuthContext:
    if config.auth.mode == "browser_auth0":
        return _browser_auth0_context(config, run_state, environ=environ)

    if config.auth.mode == "env_cookie":
        return _env_cookie_context(config, environ=environ)

    raise AuthConfigurationError(f"unsupported auth mode: {config.auth.mode}")


def _browser_auth0_context(
    config: EnvironmentConfig,
    run_state: RunState,
    *,
    environ: Mapping[str, str] | None,
) -> AuthContext:
    if environ is None:
        identities = create_run_identities(config, run_state)
    else:
        settings = settings_from_environment(config, environ=environ)
        with Auth0ManagementClient(
            settings,
            timeout_seconds=config.timeouts.request_seconds,
        ) as auth0_client:
            identities = create_run_identities(config, run_state, auth0_client=auth0_client)

    return AuthContext(
        mode="browser_auth0",
        session_cookie=identities.primary_user.session_cookie,
        identities=identities,
    )


def _env_cookie_context(
    config: EnvironmentConfig,
    *,
    environ: Mapping[str, str] | None,
) -> AuthContext:
    if config.environment_type != "local":
        raise AuthConfigurationError("auth.mode env_cookie is only allowed for local debugging")

    source = environ or os.environ
    cookie_value = source.get(config.auth.cookie_env, "").strip()
    if not cookie_value:
        raise AuthConfigurationError(
            f"required session cookie environment variable is missing: {config.auth.cookie_env}"
        )

    return AuthContext(
        mode="env_cookie",
        session_cookie=SessionCookie(
            name=config.session_cookie_name,
            value=cookie_value,
            domain="",
            path="/",
        ),
    )
