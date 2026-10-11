from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Literal, cast

import httpx
import pytest

from api_tests.auth import load_supplied_session
from api_tests.client import GatewayClient, RequestPacer
from api_tests.config import load_environment

type SessionRole = Literal["primary", "secondary"]
type AuthSmoke = Callable[[GatewayClient], None]

_AUTH_SMOKE_MODULE = importlib.import_module("tests.api.auth.test_auth_smoke")
_PRIMARY_AUTH_SMOKE = cast(
    AuthSmoke,
    _AUTH_SMOKE_MODULE.test_acquired_session_reaches_session_gateway,
)
_SECONDARY_AUTH_SMOKE = cast(
    AuthSmoke,
    _AUTH_SMOKE_MODULE.test_secondary_acquired_session_reaches_session_gateway,
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
        smoke(client)


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
        smoke(client)


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
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/auth/v1/user"
        assert request.headers["cookie"] == f"BA_SESSION={expected_cookie}"
        headers = {"location": "/login"} if response_status == httpx.codes.FOUND else None
        return httpx.Response(response_status, headers=headers)

    return GatewayClient(
        config,
        session=session,
        transport=httpx.MockTransport(handler),
        request_pacer=RequestPacer(requests_per_second=8),
    )
