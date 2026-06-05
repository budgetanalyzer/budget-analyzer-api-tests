from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
import secrets
import string
from typing import Any
from urllib.parse import urlparse

import httpx

from api_tests.config import EnvironmentConfig
from api_tests.run_state import RunState

REQUIRED_MANAGEMENT_SCOPES = "create:users"


class Auth0ManagementError(RuntimeError):
    pass


class Auth0ScopeError(Auth0ManagementError):
    pass


@dataclass(frozen=True)
class Auth0ManagementSettings:
    domain: str
    client_id: str
    client_secret: str = field(repr=False)
    connection: str
    test_email_domain: str

    @property
    def tenant_origin(self) -> str:
        return normalize_auth0_origin(self.domain)

    @property
    def management_audience(self) -> str:
        return f"{self.tenant_origin}/api/v2/"


@dataclass(frozen=True, repr=False)
class Auth0CreatedUser:
    email: str
    password: str = field(repr=False)
    user_id: str


class Auth0ManagementClient:
    def __init__(
        self,
        settings: Auth0ManagementSettings,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 15,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._management_token: str | None = None

    def __enter__(self) -> Auth0ManagementClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def request_management_api_token(self) -> str:
        response = self._client.post(
            f"{self.settings.tenant_origin}/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "audience": self.settings.management_audience,
            },
        )
        if response.status_code >= 400:
            _raise_auth0_error(
                response,
                action="request Auth0 Management API token",
                required_scope=REQUIRED_MANAGEMENT_SCOPES,
            )

        body = _json_object(response, action="request Auth0 Management API token")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise Auth0ManagementError("Auth0 token response did not include access_token")

        self._management_token = token
        return token

    def create_database_user(
        self,
        *,
        run_state: RunState,
        label: str,
        email_verified: bool = True,
    ) -> Auth0CreatedUser:
        token = self._management_token or self.request_management_api_token()
        email = generate_test_email(
            run_id=run_state.run_id,
            label=label,
            email_domain=self.settings.test_email_domain,
        )
        password = generate_password()

        response = self._client.post(
            f"{self.settings.management_audience}users",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "connection": self.settings.connection,
                "email": email,
                "password": password,
                "email_verified": email_verified,
                "verify_email": not email_verified,
            },
        )
        if response.status_code >= 400:
            _raise_auth0_error(
                response,
                action="create Auth0 database user",
                required_scope=REQUIRED_MANAGEMENT_SCOPES,
            )

        body = _json_object(response, action="create Auth0 database user")
        user_id = body.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            raise Auth0ManagementError("Auth0 create-user response did not include user_id")

        return Auth0CreatedUser(email=email, password=password, user_id=user_id)


def settings_from_environment(
    config: EnvironmentConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> Auth0ManagementSettings:
    source = environ or os.environ
    auth0_config = config.auth.auth0

    return Auth0ManagementSettings(
        domain=_required_env(source, auth0_config.management_domain_env),
        client_id=_required_env(source, auth0_config.management_client_id_env),
        client_secret=_required_env(source, auth0_config.management_client_secret_env),
        connection=_required_env(source, auth0_config.connection_env),
        test_email_domain=auth0_config.test_email_domain,
    )


def normalize_auth0_origin(domain: str) -> str:
    raw_value = domain.strip().rstrip("/")
    if not raw_value:
        raise ValueError("Auth0 domain must not be empty")

    candidate = raw_value if "://" in raw_value else f"https://{raw_value}"
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Auth0 domain must be a host name or HTTPS origin")
    if parsed.path or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Auth0 domain must not include a path, query, or fragment")

    return f"{parsed.scheme}://{parsed.netloc}"


def generate_test_email(*, run_id: str, label: str, email_domain: str) -> str:
    normalized_label = _email_safe_token(label)
    random_part = secrets.token_hex(3)
    return f"{run_id}-{normalized_label}-{random_part}@{email_domain}"


def generate_password() -> str:
    alphabet = string.ascii_letters + string.digits
    random_part = "".join(secrets.choice(alphabet) for _ in range(28))
    return f"Ba1!{random_part}"


def _required_env(source: Mapping[str, str], name: str) -> str:
    value = source.get(name, "").strip()
    if not value:
        raise Auth0ManagementError(f"required Auth0 environment variable is missing: {name}")
    return value


def _email_safe_token(value: str) -> str:
    token = "".join(character.lower() if character.isalnum() else "-" for character in value)
    token = "-".join(part for part in token.split("-") if part)
    if not token:
        raise ValueError("email label must contain at least one letter or digit")
    return token


def _json_object(response: httpx.Response, *, action: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise Auth0ManagementError(f"Auth0 response for {action} was not JSON") from exc

    if not isinstance(body, dict):
        raise Auth0ManagementError(f"Auth0 response for {action} was not a JSON object")

    return body


def _raise_auth0_error(
    response: httpx.Response,
    *,
    action: str,
    required_scope: str,
) -> None:
    body_text = response.text
    message = f"failed to {action}: HTTP {response.status_code}"
    if body_text:
        message = f"{message}: {body_text}"

    if response.status_code in {401, 403} and "scope" in body_text.lower():
        raise Auth0ScopeError(
            f"{message}. Confirm the Auth0 Management API application has scope "
            f"{required_scope!r}."
        )

    raise Auth0ManagementError(message)
