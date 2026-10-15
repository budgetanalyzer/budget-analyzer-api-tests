from __future__ import annotations

from http.cookies import CookieError, Morsel, SimpleCookie
from typing import cast

import httpx

from api_tests.logging import redacted_response_context
from api_tests.openapi import JsonValue

type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]


def assert_status(response: httpx.Response, expected_status: int) -> None:
    assert response.status_code == expected_status, redacted_response_context(response)


def assert_non_html_response(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type", "").split(";", maxsplit=1)[0].lower()
    assert content_type != "text/html", (
        f"expected non-HTML response, got content-type {response.headers.get('content-type')!r}: "
        f"{redacted_response_context(response)}"
    )

    body_prefix = response.text.lstrip()[:200].lower()
    assert not body_prefix.startswith(("<!doctype html", "<html")), (
        f"expected non-HTML response body, got: {redacted_response_context(response)}"
    )


def assert_api_error(
    response: httpx.Response,
    expected_status: int,
    *,
    error_type: str,
    code: str | None = None,
) -> JsonObject:
    body = json_object_response(response, expected_status)
    assert string_field(body, "type") == error_type
    string_field(body, "message")
    if code is not None:
        assert string_field(body, "code") == code
    return body


def assert_no_content(response: httpx.Response) -> None:
    assert_status(response, httpx.codes.NO_CONTENT)
    assert response.content == b""


def json_object_response(response: httpx.Response, expected_status: int) -> JsonObject:
    assert_status(response, expected_status)
    body: object = response.json()
    if not isinstance(body, dict) or not all(isinstance(key, str) for key in body):
        raise AssertionError(f"expected JSON object response, got: {body!r}")
    return cast(JsonObject, body)


def json_array_response(response: httpx.Response, expected_status: int) -> JsonArray:
    assert_status(response, expected_status)
    body: object = response.json()
    if not isinstance(body, list):
        raise AssertionError(f"expected JSON array response, got: {body!r}")
    return cast(JsonArray, body)


def json_scalar_response(response: httpx.Response, expected_status: int) -> JsonValue:
    assert_status(response, expected_status)
    return cast(JsonValue, response.json())


def string_field(body: JsonObject, field_name: str) -> str:
    value = body.get(field_name)
    if not isinstance(value, str) or not value:
        raise AssertionError(f"expected non-empty string field {field_name!r}, got: {value!r}")
    return value


def int_field(body: JsonObject, field_name: str) -> int:
    value = body.get(field_name)
    if not isinstance(value, int):
        raise AssertionError(f"expected integer field {field_name!r}, got: {value!r}")
    return value


def bool_field(body: JsonObject, field_name: str) -> bool:
    value = body.get(field_name)
    if not isinstance(value, bool):
        raise AssertionError(f"expected boolean field {field_name!r}, got: {value!r}")
    return value


def object_item(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AssertionError(f"expected JSON object item, got: {value!r}")
    return value


def object_field(body: JsonObject, field_name: str) -> JsonObject:
    return object_item(body.get(field_name))


def array_field(body: JsonObject, field_name: str) -> JsonArray:
    value = body.get(field_name)
    if not isinstance(value, list):
        raise AssertionError(f"expected array field {field_name!r}, got: {value!r}")
    return value


def string_array_field(body: JsonObject, field_name: str) -> list[str]:
    values = array_field(body, field_name)
    if not all(isinstance(value, str) for value in values):
        raise AssertionError(f"expected string array field {field_name!r}")
    return cast(list[str], values)


def assert_cookie_cleared(
    response: httpx.Response,
    cookie_name: str,
    *,
    expected_path: str = "/",
    expected_http_only: bool | None = None,
    expected_secure: bool | None = None,
    expected_same_site: str | None = None,
) -> None:
    """Require a scoped empty-cookie deletion without exposing its prior value."""
    cookie = _response_cookie(response, cookie_name)
    if cookie is None:
        raise AssertionError(f"expected response to clear cookie {cookie_name!r}")
    assert cookie.value == "", f"expected {cookie_name!r} clearing cookie to be empty"
    assert cookie["max-age"] == "0", f"expected {cookie_name!r} clearing cookie to set Max-Age=0"
    assert cookie["path"] == expected_path, (
        f"expected {cookie_name!r} clearing cookie path {expected_path!r}"
    )
    if expected_http_only is not None:
        assert bool(cookie["httponly"]) is expected_http_only, (
            f"expected {cookie_name!r} clearing cookie HttpOnly={expected_http_only}"
        )
    if expected_secure is not None:
        assert bool(cookie["secure"]) is expected_secure, (
            f"expected {cookie_name!r} clearing cookie Secure={expected_secure}"
        )
    if expected_same_site is not None:
        assert cookie["samesite"].casefold() == expected_same_site.casefold(), (
            f"expected {cookie_name!r} clearing cookie SameSite={expected_same_site}"
        )


def assert_cookie_not_set(response: httpx.Response, cookie_name: str) -> None:
    if _response_cookie(response, cookie_name) is not None:
        raise AssertionError(f"expected response not to set cookie {cookie_name!r}")


def _response_cookie(response: httpx.Response, cookie_name: str) -> Morsel[str] | None:
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        try:
            parsed.load(header)
        except CookieError:
            continue
        cookie = parsed.get(cookie_name)
        if cookie is not None:
            return cookie
    return None
