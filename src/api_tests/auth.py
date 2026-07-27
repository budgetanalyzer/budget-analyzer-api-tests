from __future__ import annotations

import os
from collections.abc import Mapping

from api_tests.config import EnvironmentConfig
from api_tests.identities import acquire_browser_preprovisioned_session_bundle
from api_tests.run_state import RunState
from api_tests.session import SessionBundle, SessionContext, SessionCookie, SessionIdentity


class AuthConfigurationError(RuntimeError):
    pass


def load_session_context(
    config: EnvironmentConfig,
    run_state: RunState,
    *,
    environ: Mapping[str, str] | None = None,
) -> SessionContext:
    if config.auth.mode == "browser_preprovisioned":
        return _browser_preprovisioned_context(config, run_state, environ=environ)

    if config.auth.mode == "supplied_sessions":
        return _supplied_sessions_context(config, environ=environ)

    raise AuthConfigurationError(f"unsupported auth mode: {config.auth.mode}")


def load_auth_context(
    config: EnvironmentConfig,
    run_state: RunState,
    *,
    environ: Mapping[str, str] | None = None,
) -> SessionContext:
    return load_session_context(config, run_state, environ=environ)


def _browser_preprovisioned_context(
    config: EnvironmentConfig,
    run_state: RunState,
    *,
    environ: Mapping[str, str] | None,
) -> SessionContext:
    if config.environment_type == "production":
        raise AuthConfigurationError("browser acquisition is forbidden for production")

    bundle = acquire_browser_preprovisioned_session_bundle(
        config,
        run_state,
        environ=environ,
    )

    return SessionContext(
        mode="browser_preprovisioned",
        bundle=bundle,
    )


def _supplied_sessions_context(
    config: EnvironmentConfig,
    *,
    environ: Mapping[str, str] | None,
) -> SessionContext:
    if config.environment_type == "production" and (
        config.allow_mutation or config.allow_destructive or config.data.per_run_user_boundary
    ):
        raise AuthConfigurationError(
            "production supplied_sessions requires read-only policy and no per-run users"
        )

    source = environ or os.environ
    supplied_config = config.auth.supplied_sessions
    primary_cookie_value = _required_secret(
        source,
        supplied_config.primary_session_env,
        purpose="primary supplied session",
    )
    secondary_cookie_value = _optional_secret(source, supplied_config.secondary_session_env)

    secondary = None
    if secondary_cookie_value is not None:
        secondary = SessionIdentity(
            label="secondary-session",
            session_cookie=SessionCookie(
                name=config.session_cookie_name,
                value=secondary_cookie_value,
                domain="",
                path="/",
            ),
        )

    return SessionContext(
        mode="supplied_sessions",
        bundle=SessionBundle(
            primary=SessionIdentity(
                label="primary-session",
                session_cookie=SessionCookie(
                    name=config.session_cookie_name,
                    value=primary_cookie_value,
                    domain="",
                    path="/",
                ),
            ),
            secondary=secondary,
        ),
    )


def _required_secret(source: Mapping[str, str], env_var_name: str, *, purpose: str) -> str:
    value = source.get(env_var_name, "").strip()
    if not value:
        raise AuthConfigurationError(
            f"required {purpose} environment variable is missing: {env_var_name}"
        )
    return value


def _optional_secret(source: Mapping[str, str], env_var_name: str | None) -> str | None:
    if env_var_name is None:
        return None
    value = source.get(env_var_name, "").strip()
    return value or None
