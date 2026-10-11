from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from api_tests.browser_login import (
    BrowserAccountExistsError,
    BrowserLoginCredentials,
    login_with_browser,
    signup_with_browser,
)
from api_tests.config import EnvironmentConfig
from api_tests.local_credentials import (
    CredentialGenerator,
    LocalCredentialError,
    generate_local_credentials,
    load_or_create_local_credentials,
    mark_local_credentials_complete,
)
from api_tests.session import MissingSecondarySessionError, Session

type SessionRole = Literal["primary", "secondary"]
type LoginFunction = Callable[[EnvironmentConfig, BrowserLoginCredentials], Session]
type SignupFunction = Callable[[EnvironmentConfig, BrowserLoginCredentials], Session]


class AuthConfigurationError(RuntimeError):
    pass


def acquire_session(
    config: EnvironmentConfig,
    role: SessionRole,
    *,
    environ: Mapping[str, str] | None = None,
    login: LoginFunction = login_with_browser,
    signup: SignupFunction = signup_with_browser,
    credential_path: Path | None = None,
    credential_generator: CredentialGenerator = generate_local_credentials,
) -> Session:
    if config.auth.mode == "browser_preprovisioned":
        return acquire_browser_session(
            config,
            role,
            environ=environ,
            login=login,
            signup=signup,
            credential_path=credential_path,
            credential_generator=credential_generator,
        )
    if config.auth.mode == "supplied_sessions":
        return load_supplied_session(config, role, environ=environ)
    raise AuthConfigurationError(f"unsupported auth mode: {config.auth.mode}")


def acquire_browser_session(
    config: EnvironmentConfig,
    role: SessionRole,
    *,
    environ: Mapping[str, str] | None = None,
    login: LoginFunction = login_with_browser,
    signup: SignupFunction = signup_with_browser,
    credential_path: Path | None = None,
    credential_generator: CredentialGenerator = generate_local_credentials,
) -> Session:
    if config.environment_type == "production":
        raise AuthConfigurationError("browser acquisition is forbidden for production")

    browser_config = config.auth.browser
    if browser_config is None:
        raise AuthConfigurationError(
            "browser_preprovisioned requires browser credential environment variable names"
        )

    if config.environment_type == "local":
        try:
            record = load_or_create_local_credentials(
                config,
                role,
                environ=environ,
                path=credential_path,
                generator=credential_generator,
            )
        except LocalCredentialError as exc:
            raise AuthConfigurationError(str(exc)) from exc
        if record.state != "pending":
            return login(config, record.credentials)

        try:
            session = signup(config, record.credentials)
        except BrowserAccountExistsError:
            session = login(config, record.credentials)
        try:
            mark_local_credentials_complete(
                config,
                role,
                record.credentials,
                path=credential_path,
            )
        except LocalCredentialError as exc:
            raise AuthConfigurationError(str(exc)) from exc
        return session

    if role == "primary":
        username_env = browser_config.primary_username_env
        password_env = browser_config.primary_password_env
    else:
        username_env = browser_config.secondary_username_env
        password_env = browser_config.secondary_password_env

    source = os.environ if environ is None else environ
    credentials = BrowserLoginCredentials(
        username=_required_secret(source, username_env, f"{role} username"),
        password=_required_secret(source, password_env, f"{role} password"),
    )
    return login(config, credentials)


def load_supplied_session(
    config: EnvironmentConfig,
    role: SessionRole,
    *,
    environ: Mapping[str, str] | None = None,
) -> Session:
    supplied_config = config.auth.supplied_sessions
    env_var_name = (
        supplied_config.primary_session_env
        if role == "primary"
        else supplied_config.secondary_session_env
    )
    if env_var_name is None:
        raise MissingSecondarySessionError(
            "secondary session prerequisite failed: selected environment does not configure a "
            "secondary supplied session"
        )

    source = os.environ if environ is None else environ
    cookie_value = _required_secret(source, env_var_name, f"{role} supplied session")
    return Session(cookie_name=config.session_cookie_name, cookie_value=cookie_value)


def _required_secret(source: Mapping[str, str], env_var_name: str, purpose: str) -> str:
    value = source.get(env_var_name, "").strip()
    if not value:
        raise AuthConfigurationError(
            f"required {purpose} environment variable is missing: {env_var_name}"
        )
    return value
