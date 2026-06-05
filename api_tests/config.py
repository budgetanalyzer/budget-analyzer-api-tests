from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EnvironmentType = Literal["local", "staging", "production"]
AuthMode = Literal["browser_auth0", "env_cookie"]
CleanupMode = Literal["none"]


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BrowserConfig(StrictConfigModel):
    headless: bool


class Auth0Config(StrictConfigModel):
    management_domain_env: str
    management_client_id_env: str
    management_client_secret_env: str
    connection_env: str
    test_email_domain: str

    @field_validator(
        "management_domain_env",
        "management_client_id_env",
        "management_client_secret_env",
        "connection_env",
    )
    @classmethod
    def validate_env_var_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("environment variable name must not be empty")
        if not value.replace("_", "").isalnum() or not value[0].isalpha():
            raise ValueError(f"invalid environment variable name: {value}")
        return value

    @field_validator("test_email_domain")
    @classmethod
    def validate_test_email_domain(cls, value: str) -> str:
        value = value.strip().lower()
        if not value or "@" in value or "/" in value or "." not in value:
            raise ValueError("test_email_domain must be a DNS-style domain")
        return value


class AuthConfig(StrictConfigModel):
    mode: AuthMode
    cookie_env: str
    browser: BrowserConfig
    auth0: Auth0Config

    @field_validator("cookie_env")
    @classmethod
    def validate_cookie_env(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("cookie_env must not be empty")
        if not value.replace("_", "").isalnum() or not value[0].isalpha():
            raise ValueError(f"invalid cookie_env environment variable name: {value}")
        return value


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
    verify_tls: bool
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
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("origin must be an absolute HTTP(S) URL")
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
                raise ValueError("production environments must not allow destructive tests by default")
        if self.allow_destructive and not self.allow_mutation:
            raise ValueError("allow_destructive requires allow_mutation")
        return self


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


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


def normalized_non_secret_settings(config: EnvironmentConfig) -> dict[str, Any]:
    return {
        "name": config.name,
        "origin": config.origin,
        "api_base_path": config.api_base_path,
        "openapi_path": config.openapi_path,
        "verify_tls": config.verify_tls,
        "environment_type": config.environment_type,
        "allow_mutation": config.allow_mutation,
        "allow_destructive": config.allow_destructive,
        "session_cookie_name": config.session_cookie_name,
        "auth": {
            "mode": config.auth.mode,
            "cookie_env": config.auth.cookie_env,
            "browser": config.auth.browser.model_dump(mode="json"),
            "auth0": {
                "management_domain_env": config.auth.auth0.management_domain_env,
                "management_client_id_env": config.auth.auth0.management_client_id_env,
                "management_client_secret_env": config.auth.auth0.management_client_secret_env,
                "connection_env": config.auth.auth0.connection_env,
                "test_email_domain": config.auth.auth0.test_email_domain,
            },
        },
        "data": config.data.model_dump(mode="json"),
        "timeouts": config.timeouts.model_dump(mode="json"),
    }
