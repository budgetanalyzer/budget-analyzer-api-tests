from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import (
    assert_cookie_cleared,
    assert_cookie_not_set,
    string_array_field,
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
