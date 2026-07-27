from __future__ import annotations

from pathlib import Path
from typing import Literal, Self, TypedDict
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EnvironmentType = Literal["local", "staging", "production"]
AuthMode = Literal["supplied_sessions", "browser_preprovisioned"]
CleanupMode = Literal["none"]


class SuppliedSessionNonSecretSettings(TypedDict):
    primary_session_env: str
    secondary_session_env: str | None


class BrowserNonSecretSettings(TypedDict):
    headless: bool
    primary_username_env: str
    primary_password_env: str
    secondary_username_env: str
    secondary_password_env: str


class AuthNonSecretSettings(TypedDict):
    mode: AuthMode
    supplied_sessions: SuppliedSessionNonSecretSettings
    browser: BrowserNonSecretSettings | None


class DataNonSecretSettings(TypedDict):
    namespace_prefix: str
    cleanup: CleanupMode
    per_run_user_boundary: bool


class TimeoutNonSecretSettings(TypedDict):
    request_seconds: float
    eventually_seconds: float


class EnvironmentNonSecretSettings(TypedDict):
    name: str
    origin: str
    api_base_path: str
    openapi_path: str
    environment_type: EnvironmentType
    allow_mutation: bool
    allow_destructive: bool
    session_cookie_name: str
    auth: AuthNonSecretSettings
    data: DataNonSecretSettings
    timeouts: TimeoutNonSecretSettings


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def validate_env_var_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("environment variable name must not be empty")
    if not value.replace("_", "").isalnum() or not value[0].isalpha():
        raise ValueError(f"invalid environment variable name: {value}")
    return value


class SuppliedSessionConfig(StrictConfigModel):
    primary_session_env: str
    secondary_session_env: str | None = None

    @field_validator("primary_session_env")
    @classmethod
    def validate_primary_session_env(cls, value: str) -> str:
        return validate_env_var_name(value)

    @field_validator("secondary_session_env")
    @classmethod
    def validate_secondary_session_env(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_env_var_name(value)


class BrowserConfig(StrictConfigModel):
    headless: bool
    primary_username_env: str
    primary_password_env: str
    secondary_username_env: str
    secondary_password_env: str

    @field_validator(
        "primary_username_env",
        "primary_password_env",
        "secondary_username_env",
        "secondary_password_env",
    )
    @classmethod
    def validate_secret_env_name(cls, value: str) -> str:
        return validate_env_var_name(value)


class AuthConfig(StrictConfigModel):
    mode: AuthMode
    supplied_sessions: SuppliedSessionConfig
    browser: BrowserConfig | None = None


class DataConfig(StrictConfigModel):
    namespace_prefix: str
    cleanup: CleanupMode
    per_run_user_boundary: bool

    @field_validator("namespace_prefix")
    @classmethod
    def validate_namespace_prefix(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("namespace_prefix must not be empty")
        return value


class TimeoutConfig(StrictConfigModel):
    request_seconds: float = Field(gt=0)
    eventually_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_timeout_order(self) -> Self:
        if self.eventually_seconds < self.request_seconds:
            raise ValueError("eventually_seconds must be greater than or equal to request_seconds")
        return self


class EnvironmentConfig(StrictConfigModel):
    name: str
    origin: str
    api_base_path: str
    openapi_path: str
    environment_type: EnvironmentType
    allow_mutation: bool
    allow_destructive: bool
    session_cookie_name: str
    auth: AuthConfig
    data: DataConfig
    timeouts: TimeoutConfig

    @field_validator("name", "session_cookie_name")
    @classmethod
    def validate_non_empty_string(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("origin must be an absolute HTTPS URL")
        if parsed.path or parsed.params or parsed.query or parsed.fragment:
            raise ValueError("origin must not include a path, query, or fragment")
        return value

    @field_validator("api_base_path", "openapi_path")
    @classmethod
    def validate_absolute_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/"):
            raise ValueError("path must start with /")
        if "//" in value:
            raise ValueError("path must not contain duplicate slashes")
        return value.rstrip("/") if value != "/" else value

    @model_validator(mode="after")
    def validate_environment_policy(self) -> Self:
        if self.environment_type == "production":
            if self.allow_mutation:
                raise ValueError("production environments must not allow mutation by default")
            if self.allow_destructive:
                raise ValueError(
                    "production environments must not allow destructive tests by default"
                )
            if self.auth.mode != "supplied_sessions":
                raise ValueError("production auth.mode must use supplied_sessions")
            if self.auth.browser is not None:
                raise ValueError("production environments must not configure browser acquisition")
            if self.data.per_run_user_boundary:
                raise ValueError("production environments must not create per-run users")
        if self.environment_type == "staging":
            if (
                self.auth.mode == "supplied_sessions"
                and self.auth.supplied_sessions.secondary_session_env is None
            ):
                raise ValueError("staging supplied_sessions requires secondary_session_env")
            if self.auth.mode == "browser_preprovisioned" and self.auth.browser is None:
                raise ValueError("staging browser_preprovisioned requires browser settings")
        if self.auth.mode == "browser_preprovisioned":
            if self.environment_type == "production":
                raise ValueError("browser acquisition is forbidden for production")
            if self.auth.browser is None:
                raise ValueError("browser_preprovisioned requires browser settings")
        if self.allow_destructive and not self.allow_mutation:
            raise ValueError("allow_destructive requires allow_mutation")
        return self


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def environments_dir() -> Path:
    return repository_root() / "environments"


def environment_path(name: str, base_dir: Path | None = None) -> Path:
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise ValueError(f"invalid environment name: {name}")

    directory = base_dir or environments_dir()
    path = directory / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"environment file not found: {path}")
    return path


def load_environment(name: str, base_dir: Path | None = None) -> EnvironmentConfig:
    return load_environment_file(environment_path(name, base_dir))


def load_environment_file(path: Path) -> EnvironmentConfig:
    with path.open("r", encoding="utf-8") as config_file:
        raw_config = yaml.safe_load(config_file)

    if not isinstance(raw_config, dict):
        raise ValueError(f"environment file must contain a YAML mapping: {path}")

    return EnvironmentConfig.model_validate(raw_config)


def with_session_mode(config: EnvironmentConfig, mode: AuthMode | None) -> EnvironmentConfig:
    if mode is None or mode == config.auth.mode:
        return config

    raw_config = config.model_dump()
    auth_config = raw_config["auth"]
    if not isinstance(auth_config, dict):
        raise TypeError("normalized auth config was not a mapping")
    auth_config["mode"] = mode
    return EnvironmentConfig.model_validate(raw_config)


def normalized_non_secret_settings(config: EnvironmentConfig) -> EnvironmentNonSecretSettings:
    return {
        "name": config.name,
        "origin": config.origin,
        "api_base_path": config.api_base_path,
        "openapi_path": config.openapi_path,
        "environment_type": config.environment_type,
        "allow_mutation": config.allow_mutation,
        "allow_destructive": config.allow_destructive,
        "session_cookie_name": config.session_cookie_name,
        "auth": {
            "mode": config.auth.mode,
            "supplied_sessions": {
                "primary_session_env": config.auth.supplied_sessions.primary_session_env,
                "secondary_session_env": config.auth.supplied_sessions.secondary_session_env,
            },
            "browser": {
                "headless": config.auth.browser.headless,
                "primary_username_env": config.auth.browser.primary_username_env,
                "primary_password_env": config.auth.browser.primary_password_env,
                "secondary_username_env": config.auth.browser.secondary_username_env,
                "secondary_password_env": config.auth.browser.secondary_password_env,
            }
            if config.auth.browser is not None
            else None,
        },
        "data": {
            "namespace_prefix": config.data.namespace_prefix,
            "cleanup": config.data.cleanup,
            "per_run_user_boundary": config.data.per_run_user_boundary,
        },
        "timeouts": {
            "request_seconds": config.timeouts.request_seconds,
            "eventually_seconds": config.timeouts.eventually_seconds,
        },
    }
