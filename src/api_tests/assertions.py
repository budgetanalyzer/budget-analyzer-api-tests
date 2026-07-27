from __future__ import annotations

from typing import cast

import httpx

from api_tests.openapi import JsonValue

type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]


def assert_status(response: httpx.Response, expected_status: int) -> None:
    assert response.status_code == expected_status, response.text


def assert_status_in(response: httpx.Response, expected_statuses: set[int]) -> None:
    assert response.status_code in expected_statuses, response.text


def assert_non_html_response(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type", "").split(";", maxsplit=1)[0].lower()
    assert content_type != "text/html", (
        f"expected non-HTML response, got content-type {response.headers.get('content-type')!r}: "
        f"{response.text[:500]}"
    )

    body_prefix = response.text.lstrip()[:200].lower()
    assert not body_prefix.startswith(("<!doctype html", "<html")), (
        f"expected non-HTML response body, got: {response.text[:500]!r}"
    )


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
