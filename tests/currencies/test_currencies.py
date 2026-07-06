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
from api_tests.openapi import JsonValue


@pytest.mark.openapi("getAll")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_all_currencies_returns_currency_series(gateway_client: GatewayClient) -> None:
    response = gateway_client.api_request("GET", "/v1/currencies", params={"enabledOnly": False})

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
def test_get_currency_by_id_returns_currency_series(gateway_client: GatewayClient) -> None:
    currencies_response = gateway_client.api_request(
        "GET",
        "/v1/currencies",
        params={"enabledOnly": False},
    )
    currency_id = _first_currency_id(json_array_response(currencies_response, httpx.codes.OK))

    response = gateway_client.api_request("GET", f"/v1/currencies/{currency_id}")

    currency = json_object_response(response, httpx.codes.OK)
    assert int_field(currency, "id") == currency_id
    string_field(currency, "currencyCode")
    string_field(currency, "providerSeriesId")


def _first_currency_id(currencies: list[JsonValue]) -> int:
    if not currencies:
        pytest.skip("environment has no currency series to fetch by id")

    return int_field(object_item(currencies[0]), "id")
