from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

import httpx
import pytest

from api_tests.auth import load_supplied_session
from api_tests.client import (
    GatewayClient,
    GatewayRequestPacing,
    RequestPacer,
)
from api_tests.config import load_environment
from api_tests.openapi import OpenApiDocument, load_snapshot
from api_tests.session_gateway_assertions import REQUIRED_ADMIN_PERMISSIONS, AuthenticatedIdentity

type SessionRole = Literal["primary", "secondary", "admin"]
type AuthSmoke = Callable[[GatewayClient, OpenApiDocument], None]
type AdminAuthSmoke = Callable[[GatewayClient, AuthenticatedIdentity, OpenApiDocument], None]

_AUTH_SMOKE_MODULE = importlib.import_module("tests.api.session_gateway.test_user")
_PRIMARY_AUTH_SMOKE = cast(
    AuthSmoke,
    _AUTH_SMOKE_MODULE.test_acquired_session_reaches_session_gateway,
)
_SECONDARY_AUTH_SMOKE = cast(
    AuthSmoke,
    _AUTH_SMOKE_MODULE.test_secondary_acquired_session_reaches_session_gateway,
)
_ADMIN_AUTH_SMOKE = cast(
    AdminAuthSmoke,
    _AUTH_SMOKE_MODULE.test_admin_acquired_session_has_required_authorization,
)
_SMOKE_CASES: tuple[tuple[SessionRole, AuthSmoke, str], ...] = (
    ("primary", _PRIMARY_AUTH_SMOKE, "primary-cookie"),
    ("secondary", _SECONDARY_AUTH_SMOKE, "secondary-cookie"),
)


@pytest.mark.parametrize(("role", "smoke", "expected_cookie"), _SMOKE_CASES)
def test_auth_smoke_accepts_valid_supplied_session(
    role: SessionRole,
    smoke: AuthSmoke,
    expected_cookie: str,
) -> None:
    with _mocked_supplied_session_client(
        role,
        expected_cookie=expected_cookie,
        response_status=httpx.codes.OK,
    ) as client:
        smoke(client, _session_gateway_snapshot())


@pytest.mark.parametrize(("role", "smoke", "expected_cookie"), _SMOKE_CASES)
@pytest.mark.parametrize("response_status", [httpx.codes.UNAUTHORIZED, httpx.codes.FOUND])
def test_auth_smoke_rejects_invalid_supplied_session(
    role: SessionRole,
    smoke: AuthSmoke,
    expected_cookie: str,
    response_status: int,
) -> None:
    with (
        _mocked_supplied_session_client(
            role,
            expected_cookie=expected_cookie,
            response_status=response_status,
        ) as client,
        pytest.raises(AssertionError, match=f"HTTP {response_status}"),
    ):
        smoke(client, _session_gateway_snapshot())


def test_admin_auth_smoke_accepts_matching_validated_identity() -> None:
    identity = AuthenticatedIdentity(
        subject="idp|admin",
        roles=frozenset({"USER", "ADMIN"}),
        permissions=REQUIRED_ADMIN_PERMISSIONS,
        display_name="Test User",
        email="test@example.invalid",
    )
    with _mocked_supplied_session_client(
        "admin",
        expected_cookie="admin-cookie",
        response_status=httpx.codes.OK,
    ) as client:
        _ADMIN_AUTH_SMOKE(client, identity, _session_gateway_snapshot())


def _mocked_supplied_session_client(
    role: SessionRole,
    *,
    expected_cookie: str,
    response_status: int,
) -> GatewayClient:
    config = load_environment("local")
    session = load_supplied_session(
        config,
        role,
        environ={
            "BA_PRIMARY_SESSION": "primary-cookie",
            "BA_SECONDARY_SESSION": "secondary-cookie",
            "BA_ADMIN_SESSION": "admin-cookie",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/auth/v1/user"
        assert request.headers["cookie"] == f"BA_SESSION={expected_cookie}"
        if response_status == httpx.codes.OK:
            return httpx.Response(
                response_status,
                json={
                    "sub": "idp|admin" if role == "admin" else "idp|subject",
                    "name": "Test User",
                    "email": "test@example.invalid",
                    "picture": "https://example.invalid/picture.png",
                    "authenticated": True,
                    "roles": ["USER", "ADMIN"] if role == "admin" else ["USER"],
                    "permissions": (
                        sorted(REQUIRED_ADMIN_PERMISSIONS)
                        if role == "admin"
                        else ["transactions:read"]
                    ),
                },
            )
        headers = {"location": "/login"} if response_status == httpx.codes.FOUND else None
        return httpx.Response(response_status, headers=headers)

    return GatewayClient(
        config,
        session=session,
        transport=httpx.MockTransport(handler),
        request_pacing=GatewayRequestPacing(
            login_initiation_pacer=RequestPacer(requests_per_second=1_000_000),
            login_completion_pacer=RequestPacer(requests_per_second=1_000_000),
            session_operations_pacer=RequestPacer(requests_per_second=1_000_000),
            api_requests_pacer=RequestPacer(requests_per_second=1_000_000),
        ),
    )


def _session_gateway_snapshot() -> OpenApiDocument:
    return load_snapshot(Path("schemas/session-gateway-openapi.yaml"))
