from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import (
    bool_field,
    int_field,
    json_array_response,
    json_object_response,
    object_item,
    string_field,
)
from api_tests.client import GatewayClient
from api_tests.openapi import JsonValue, OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi


@pytest.mark.openapi("getAll")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_all_currencies_returns_currency_series(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request("GET", "/v1/currencies", params={"enabledOnly": False})

    assert_response_matches_openapi(response, "getAll", httpx.codes.OK, openapi_snapshot)
    currencies = json_array_response(response, httpx.codes.OK)
    for item in currencies:
        currency = object_item(item)
        int_field(currency, "id")
        string_field(currency, "currencyCode")
        string_field(currency, "providerSeriesId")
        bool_field(currency, "enabled")


@pytest.mark.openapi("getById")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_currency_by_id_returns_currency_series(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    currencies_response = gateway_client.api_request(
        "GET",
        "/v1/currencies",
        params={"enabledOnly": False},
    )
    assert_response_matches_openapi(currencies_response, "getAll", httpx.codes.OK, openapi_snapshot)
    currency_id = _first_currency_id(json_array_response(currencies_response, httpx.codes.OK))

    response = gateway_client.api_request("GET", f"/v1/currencies/{currency_id}")

    assert_response_matches_openapi(response, "getById", httpx.codes.OK, openapi_snapshot)
    currency = json_object_response(response, httpx.codes.OK)
    assert int_field(currency, "id") == currency_id
    string_field(currency, "currencyCode")
    string_field(currency, "providerSeriesId")


@pytest.mark.openapi("getById")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_currency_unknown_id_returns_not_found(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request("GET", "/v1/currencies/9223372036854775807")

    assert_response_matches_openapi(response, "getById", httpx.codes.NOT_FOUND, openapi_snapshot)
    json_object_response(response, httpx.codes.NOT_FOUND)


def _first_currency_id(currencies: list[JsonValue]) -> int:
    if not currencies:
        pytest.skip("environment has no currency series to fetch by id")

    return int_field(object_item(currencies[0]), "id")
