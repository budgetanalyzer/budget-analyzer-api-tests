from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import (
    assert_cookie_cleared,
    assert_cookie_not_set,
    string_array_field,
)
from api_tests.browser_login import BrowserCookieMetadata, BrowserLoginResult
from api_tests.config import load_environment
from api_tests.session import Session
from api_tests.session_gateway_assertions import (
    assert_browser_login_contract,
    assert_controlled_callback_failure_redirect,
    assert_logout_redirect,
    assert_oauth_authorization_redirect,
)


def test_string_array_field_accepts_empty_and_populated_arrays() -> None:
    assert string_array_field({"roles": []}, "roles") == []
    assert string_array_field({"roles": ["USER", "AUDITOR"]}, "roles") == [
        "USER",
        "AUDITOR",
    ]


def test_string_array_field_rejects_non_string_items() -> None:
    with pytest.raises(AssertionError, match="expected string array field 'roles'"):
        string_array_field({"roles": ["USER", 7]}, "roles")


def test_assert_cookie_cleared_accepts_empty_max_age_zero_cookie() -> None:
    response = httpx.Response(
        401,
        headers={"set-cookie": "BA_SESSION=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict"},
    )

    assert_cookie_cleared(response, "BA_SESSION")


def test_assert_cookie_cleared_does_not_expose_nonempty_value() -> None:
    response = httpx.Response(
        401,
        headers={"set-cookie": "BA_SESSION=sensitive-invalid-value; Path=/; Max-Age=0"},
    )

    with pytest.raises(AssertionError) as exc_info:
        assert_cookie_cleared(response, "BA_SESSION")

    assert "sensitive-invalid-value" not in str(exc_info.value)


def test_assert_cookie_not_set_ignores_unrelated_cookie() -> None:
    response = httpx.Response(401, headers={"set-cookie": "SESSION=framework; Path=/"})

    assert_cookie_not_set(response, "BA_SESSION")


def test_assert_cookie_not_set_rejects_public_cookie() -> None:
    response = httpx.Response(401, headers={"set-cookie": "BA_SESSION=; Path=/; Max-Age=0"})

    with pytest.raises(AssertionError, match="expected response not to set cookie 'BA_SESSION'"):
        assert_cookie_not_set(response, "BA_SESSION")


def test_authorization_redirect_parser_validates_oidc_and_pkce_without_repr_secrets() -> None:
    config = load_environment("local")
    response = httpx.Response(
        httpx.codes.FOUND,
        headers={
            "location": (
                "https://tenant.us.auth0.com/authorize?response_type=code"
                "&redirect_uri=https%3A%2F%2Fapp.budgetanalyzer.localhost%2Flogin%2Foauth2%2Fcode%2Fidp"
                "&scope=openid%20profile%20email&state=sensitive-state"
                "&code_challenge=sensitive-challenge&code_challenge_method=S256"
            )
        },
    )

    redirect = assert_oauth_authorization_redirect(response, config)

    assert redirect.provider_host == "tenant.us.auth0.com"
    assert redirect.state == "sensitive-state"
    assert "sensitive-state" not in repr(redirect)


def test_controlled_callback_failure_parser_accepts_safe_return_url() -> None:
    response = httpx.Response(
        httpx.codes.FOUND,
        headers={"location": "/login?error=auth_failed&returnUrl=%2Fpeace"},
    )

    assert_controlled_callback_failure_redirect(response, expected_return_url="/peace")


def test_browser_cookie_contract_uses_public_cookie_and_safe_metadata_only() -> None:
    config = load_environment("local")
    result = BrowserLoginResult(
        session=Session(cookie_name="BA_SESSION", cookie_value="sensitive-session"),
        session_cookie=BrowserCookieMetadata(
            name="BA_SESSION",
            domain="app.budgetanalyzer.localhost",
            path="/",
            http_only=True,
            secure=True,
            same_site="Strict",
        ),
        cookie_names=("BA_SESSION", "SESSION"),
        final_path="/peace",
        callback_observed=True,
        observations=(),
    )

    assert_browser_login_contract(result, config, expected_final_path="/peace")
    assert "sensitive-session" not in repr(result)


def test_logout_redirect_parser_requires_provider_return_target_and_cookie_contract() -> None:
    config = load_environment("local")
    response = httpx.Response(
        httpx.codes.FOUND,
        headers={
            "location": (
                "https://tenant.us.auth0.com/v2/logout?"
                "returnTo=https%3A%2F%2Fapp.budgetanalyzer.localhost%2Fpeace"
                "&client_id=environment-specific"
            ),
            "set-cookie": ("BA_SESSION=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict"),
        },
    )

    assert assert_logout_redirect(response, config) == "tenant.us.auth0.com"
