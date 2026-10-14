from __future__ import annotations

import httpx

from api_tests.assertions import (
    JsonObject,
    bool_field,
    json_object_response,
    string_array_field,
    string_field,
)
from api_tests.openapi import OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi


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
