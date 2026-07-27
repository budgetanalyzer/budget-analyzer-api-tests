from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from api_tests.browser_login import BrowserLoginCredentials, login_with_browser
from api_tests.config import EnvironmentConfig
from api_tests.run_state import RunState
from api_tests.session import SessionBundle, SessionCookie, SessionIdentity


class PreprovisionedSessionError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class PreprovisionedUserCredentials:
    label: str
    username: str
    password: str = field(repr=False)


LoginFunction = Callable[[EnvironmentConfig, BrowserLoginCredentials], SessionCookie]


def acquire_browser_preprovisioned_session_bundle(
    config: EnvironmentConfig,
    run_state: RunState,
    *,
    environ: Mapping[str, str] | None = None,
    login: LoginFunction = login_with_browser,
) -> SessionBundle:
    del run_state
    browser_config = config.auth.browser
    if browser_config is None:
        raise PreprovisionedSessionError(
            "browser_preprovisioned requires browser credential environment variable names"
        )

    source = environ or os.environ
    primary_credentials = PreprovisionedUserCredentials(
        label="primary-user",
        username=_required_secret(source, browser_config.primary_username_env, "primary username"),
        password=_required_secret(source, browser_config.primary_password_env, "primary password"),
    )
    secondary_credentials = PreprovisionedUserCredentials(
        label="secondary-user",
        username=_required_secret(
            source, browser_config.secondary_username_env, "secondary username"
        ),
        password=_required_secret(
            source, browser_config.secondary_password_env, "secondary password"
        ),
    )

    primary_cookie = login(
        config,
        BrowserLoginCredentials(
            username=primary_credentials.username,
            password=primary_credentials.password,
        ),
    )
    secondary_cookie = login(
        config,
        BrowserLoginCredentials(
            username=secondary_credentials.username,
            password=secondary_credentials.password,
        ),
    )

    return SessionBundle(
        primary=_session_identity(primary_credentials, primary_cookie),
        secondary=_session_identity(secondary_credentials, secondary_cookie),
    )


def _session_identity(
    credentials: PreprovisionedUserCredentials,
    cookie: SessionCookie,
) -> SessionIdentity:
    return SessionIdentity(label=credentials.label, session_cookie=cookie)


def _required_secret(source: Mapping[str, str], env_var_name: str, purpose: str) -> str:
    value = source.get(env_var_name, "").strip()
    if not value:
        raise PreprovisionedSessionError(
            f"required pre-provisioned {purpose} environment variable is missing: {env_var_name}"
        )
    return value
