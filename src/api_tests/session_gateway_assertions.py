from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import SplitResult, parse_qs, quote_plus, urlsplit

import httpx

from api_tests.assertions import (
    JsonObject,
    assert_cookie_cleared,
    assert_status,
    bool_field,
    json_object_response,
    string_array_field,
    string_field,
)
from api_tests.browser_login import BrowserLoginResult
from api_tests.config import EnvironmentConfig
from api_tests.openapi import OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi


@dataclass(frozen=True, repr=False)
class OAuthAuthorizationRedirect:
    provider_host: str
    state: str = field(repr=False)


def assert_authenticated_user_response(
    response: httpx.Response,
    openapi_snapshot: OpenApiDocument,
) -> JsonObject:
    """Validate the schema and stable semantics of an authenticated public user response."""
    assert_response_matches_openapi(
        response,
        "getCurrentUser",
        httpx.codes.OK,
        openapi_snapshot,
    )
    body = json_object_response(response, httpx.codes.OK)
    assert bool_field(body, "authenticated") is True
    for field_name in ("sub", "name", "email"):
        string_field(body, field_name)
    string_array_field(body, "roles")
    string_array_field(body, "permissions")
    return body


def assert_oauth_authorization_redirect(
    response: httpx.Response,
    config: EnvironmentConfig,
) -> OAuthAuthorizationRedirect:
    assert_status(response, httpx.codes.FOUND)
    location = response.headers.get("location")
    if location is None:
        raise AssertionError("OAuth authorization redirect did not provide a Location header")
    parsed = urlsplit(location)
    provider_host = _assert_allowlisted_provider_uri(parsed, config)
    query = parse_qs(parsed.query, keep_blank_values=True)
    assert _single_query_value(query, "response_type") == "code", (
        "OAuth authorization redirect must use authorization-code flow"
    )
    callback_uri = _single_query_value(query, "redirect_uri")
    assert callback_uri == f"{config.origin}{config.browser_auth_contract.callback_path}", (
        "OAuth authorization redirect used an unexpected callback URI"
    )
    state = _single_query_value(query, "state")
    assert state, "OAuth authorization redirect state must not be empty"
    scopes = frozenset(_single_query_value(query, "scope").split())
    assert {"openid", "profile", "email"}.issubset(scopes), (
        "OAuth authorization redirect omitted required OIDC scopes"
    )
    assert _single_query_value(query, "code_challenge"), (
        "OAuth authorization redirect omitted the PKCE challenge"
    )
    assert _single_query_value(query, "code_challenge_method") == "S256", (
        "OAuth authorization redirect must use PKCE S256"
    )
    return OAuthAuthorizationRedirect(provider_host=provider_host, state=state)


def assert_controlled_callback_failure_redirect(
    response: httpx.Response,
    *,
    expected_return_url: str | None = None,
) -> None:
    assert_status(response, httpx.codes.FOUND)
    location = response.headers.get("location")
    if location is None:
        raise AssertionError("OAuth callback failure did not provide a Location header")
    parsed = urlsplit(location)
    assert not parsed.scheme and not parsed.netloc and parsed.path == "/login", (
        "OAuth callback failure did not use the controlled same-origin login path"
    )
    query = parse_qs(parsed.query, keep_blank_values=True)
    assert _single_query_value(query, "error") == "auth_failed", (
        "OAuth callback failure did not use the documented error classification"
    )
    return_urls = query.get("returnUrl", [])
    if expected_return_url is None:
        assert not return_urls, "OAuth callback failure unexpectedly retained a return URL"
    else:
        assert return_urls == [expected_return_url], (
            "OAuth callback failure did not preserve the expected safe return URL"
        )


def assert_browser_login_contract(
    result: BrowserLoginResult,
    config: EnvironmentConfig,
    *,
    expected_final_path: str,
) -> None:
    assert result.callback_observed, "OAuth callback path was not observed"
    assert result.final_path == expected_final_path, (
        "browser login finished on an unexpected same-origin path"
    )
    cookie = result.session_cookie
    assert cookie.name == config.session_cookie_name
    assert cookie.path == "/"
    assert cookie.http_only is True
    assert cookie.secure is True
    assert cookie.same_site == config.browser_auth_contract.session_cookie_same_site
    assert result.session.cookie_name == config.session_cookie_name
    assert result.session.cookie_name != "SESSION", (
        "the framework SESSION cookie must not become the public authentication contract"
    )


def assert_logout_redirect(
    response: httpx.Response,
    config: EnvironmentConfig,
) -> str:
    assert_status(response, httpx.codes.FOUND)
    location = response.headers.get("location")
    if location is None:
        raise AssertionError("logout response did not provide a Location header")
    parsed = urlsplit(location)
    provider_host = _assert_allowlisted_provider_uri(parsed, config)
    assert parsed.path == config.browser_auth_contract.provider_logout_path, (
        "logout redirect used an unexpected identity-provider endpoint"
    )
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected_return_to = f"{config.origin}{config.browser_auth_contract.logout_return_path}"
    assert f"returnTo={quote_plus(expected_return_to)}" in parsed.query, (
        "logout redirect did not encode its configured return target"
    )
    assert _single_query_value(query, "returnTo") == expected_return_to, (
        "logout redirect used an unexpected configured return target"
    )
    assert _single_query_value(query, "client_id"), (
        "logout redirect omitted its identity-provider client identifier"
    )
    assert_cookie_cleared(
        response,
        config.session_cookie_name,
        expected_http_only=True,
        expected_secure=True,
        expected_same_site=config.browser_auth_contract.session_cookie_same_site,
    )
    return provider_host


def _assert_allowlisted_provider_uri(
    parsed: SplitResult,
    config: EnvironmentConfig,
) -> str:
    scheme = parsed.scheme
    host = parsed.hostname
    if scheme != "https" or host is None:
        raise AssertionError("identity-provider redirect must use an absolute HTTPS URI")
    normalized_host = host.casefold()
    if not any(
        normalized_host == suffix or normalized_host.endswith(f".{suffix}")
        for suffix in config.browser_auth_contract.provider_host_suffixes
    ):
        raise AssertionError("identity-provider redirect host was not allowlisted")
    return normalized_host


def _single_query_value(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name, [])
    if len(values) != 1 or not values[0]:
        raise AssertionError(f"redirect query parameter {name!r} must occur exactly once")
    return values[0]
