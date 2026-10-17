from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
    DisposableCredentialRecord,
    LocalCredentialError,
    generate_local_credentials,
    load_or_create_disposable_credentials,
    load_or_create_local_credentials,
    mark_disposable_credentials_complete,
    mark_local_credentials_complete,
)
from api_tests.logging import redact_sensitive_text
from api_tests.session import MissingAdminSessionError, MissingSecondarySessionError, Session

type SessionRole = Literal["primary", "secondary", "admin"]
type LoginFunction = Callable[[EnvironmentConfig, BrowserLoginCredentials], Session]
type SignupFunction = Callable[[EnvironmentConfig, BrowserLoginCredentials], Session]


class AuthConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class ProvisionedDisposableTarget:
    record: DisposableCredentialRecord
    first_session: Session


def acquire_disposable_browser_target(
    config: EnvironmentConfig,
    run_id: str,
    scenario_id: str,
    *,
    login: LoginFunction | None = None,
    signup: SignupFunction = signup_with_browser,
    credential_path: Path | None = None,
    credential_generator: CredentialGenerator = generate_local_credentials,
) -> ProvisionedDisposableTarget:
    """Provision only the current exact-local deactivation target through public signup."""
    if config.auth.mode != "browser_preprovisioned":
        raise AuthConfigurationError(
            "admin deactivation requires exact-local browser_preprovisioned mode; opaque "
            "supplied sessions cannot establish disposable target ownership"
        )
    if not config.allow_mutation or not config.allow_destructive:
        raise AuthConfigurationError(
            "admin deactivation requires both mutation and destructive environment permissions"
        )
    try:
        record = load_or_create_disposable_credentials(
            config,
            run_id,
            scenario_id,
            path=credential_path,
            generator=credential_generator,
        )
    except LocalCredentialError as exc:
        raise AuthConfigurationError(str(exc)) from exc

    if record.state == "complete":
        session = _login_browser_account(config, "primary", record.credentials, login=login)
        return ProvisionedDisposableTarget(record, session)

    try:
        session = signup(config, record.credentials)
    except BrowserAccountExistsError:
        session = _login_browser_account(config, "primary", record.credentials, login=login)
    try:
        completed = mark_disposable_credentials_complete(config, record, path=credential_path)
    except LocalCredentialError as exc:
        raise AuthConfigurationError(str(exc)) from exc
    return ProvisionedDisposableTarget(completed, session)


def acquire_session(
    config: EnvironmentConfig,
    role: SessionRole,
    *,
    environ: Mapping[str, str] | None = None,
    login: LoginFunction | None = None,
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
    login: LoginFunction | None = None,
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
            return _login_browser_account(config, role, record.credentials, login=login)

        try:
            session = signup(config, record.credentials)
        except BrowserAccountExistsError:
            session = _login_browser_account(config, role, record.credentials, login=login)
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

    username_env, password_env = _browser_variable_names(config, role)

    source = os.environ if environ is None else environ
    credentials = BrowserLoginCredentials(
        username=_required_secret(source, username_env, f"{role} username"),
        password=_required_secret(source, password_env, f"{role} password"),
    )
    return _login_browser_account(config, role, credentials, login=login)


def load_existing_browser_credentials(
    config: EnvironmentConfig,
    role: SessionRole,
    *,
    environ: Mapping[str, str] | None = None,
    credential_path: Path | None = None,
) -> BrowserLoginCredentials:
    """Load an already provisioned browser identity without creating a session or account."""
    if config.auth.mode != "browser_preprovisioned":
        raise AuthConfigurationError(
            "isolated browser lifecycle requires auth.mode browser_preprovisioned; supplied "
            "sessions cannot create a test-owned session"
        )
    if config.environment_type == "production":
        raise AuthConfigurationError("browser lifecycle acquisition is forbidden for production")

    browser_config = config.auth.browser
    if browser_config is None:
        raise AuthConfigurationError("browser_preprovisioned requires browser settings")

    if config.environment_type == "local":
        try:
            record = load_or_create_local_credentials(
                config,
                role,
                environ=environ,
                path=credential_path,
            )
        except LocalCredentialError as exc:
            raise AuthConfigurationError(str(exc)) from exc
        if record.state == "pending":
            raise AuthConfigurationError(
                f"{role} browser identity is not provisioned; acquire the reusable session first"
            )
        return record.credentials

    username_env, password_env = _browser_variable_names(config, role)
    source = os.environ if environ is None else environ
    return BrowserLoginCredentials(
        username=_required_secret(source, username_env, f"{role} username"),
        password=_required_secret(source, password_env, f"{role} password"),
    )


def load_supplied_session(
    config: EnvironmentConfig,
    role: SessionRole,
    *,
    environ: Mapping[str, str] | None = None,
) -> Session:
    supplied_config = config.auth.supplied_sessions
    env_var_name: str | None
    if role == "primary":
        env_var_name = supplied_config.primary_session_env
    elif role == "secondary":
        env_var_name = supplied_config.secondary_session_env
    else:
        env_var_name = supplied_config.admin_session_env
    if env_var_name is None:
        if role == "secondary":
            raise MissingSecondarySessionError(
                "secondary session prerequisite failed: selected environment does not configure "
                "a secondary supplied session"
            )
        raise MissingAdminSessionError(
            "admin session prerequisite failed: selected environment does not configure an "
            "admin supplied session"
        )

    source = os.environ if environ is None else environ
    cookie_value = _required_secret(source, env_var_name, f"{role} supplied session")
    return Session(cookie_name=config.session_cookie_name, cookie_value=cookie_value)


def admin_promotion_message(
    config: EnvironmentConfig,
    subject: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Describe the external ADMIN assignment needed for an authenticated normal user."""
    source = os.environ if environ is None else environ
    safe_subject = redact_sensitive_text(subject)
    if config.auth.mode == "supplied_sessions":
        session_env = config.auth.supplied_sessions.admin_session_env
        configured_source = session_env or "the configured admin session variable"
        identity_line = (
            f"The admin session is supplied by {configured_source}; its public subject is "
            f"{safe_subject!r}. After changing grants, supply a refreshed session."
        )
    else:
        browser_config = config.auth.browser
        if browser_config is None:
            raise AuthConfigurationError("browser_preprovisioned requires browser settings")
        username_env = browser_config.admin_username_env
        password_env = browser_config.admin_password_env
        if source.get(username_env, "").strip() and source.get(password_env, "").strip():
            identity_line = (
                f"The admin credentials are supplied by {username_env} and "
                f"{password_env}; the public subject is {safe_subject!r}."
            )
        elif config.environment_type == "local":
            identity_line = (
                "The local account username is stored as BA_ADMIN_TEST_USERNAME in .env.local."
            )
        else:
            identity_line = (
                f"The pre-provisioned admin credentials are supplied by {username_env} and "
                f"{password_env}; the public subject is {safe_subject!r}."
            )
    return (
        "Please promote the test admin user to the ADMIN role in the database.\n"
        "Add ADMIN while retaining USER. "
        f"{identity_line}\n"
        "Then rerun the same command."
    )


def _browser_variable_names(
    config: EnvironmentConfig,
    role: SessionRole,
) -> tuple[str, str]:
    browser_config = config.auth.browser
    if browser_config is None:
        raise AuthConfigurationError("browser_preprovisioned requires browser settings")
    if role == "primary":
        return browser_config.primary_username_env, browser_config.primary_password_env
    if role == "secondary":
        return browser_config.secondary_username_env, browser_config.secondary_password_env
    return browser_config.admin_username_env, browser_config.admin_password_env


def _login_browser_account(
    config: EnvironmentConfig,
    role: SessionRole,
    credentials: BrowserLoginCredentials,
    *,
    login: LoginFunction | None,
) -> Session:
    if login is not None:
        return login(config, credentials)
    expected_return_paths: tuple[str, ...] = (config.browser_auth_contract.default_return_path,)
    if role == "admin":
        expected_return_paths += (config.browser_auth_contract.admin_return_path,)
    return login_with_browser(
        config,
        credentials,
        expected_return_paths=expected_return_paths,
    )


def _required_secret(source: Mapping[str, str], env_var_name: str, purpose: str) -> str:
    value = source.get(env_var_name, "").strip()
    if not value:
        raise AuthConfigurationError(
            f"required {purpose} environment variable is missing: {env_var_name}"
        )
    return value
