from __future__ import annotations

import httpx
import pytest

from api_tests.assertions import (
    assert_status_in,
    json_array_response,
    json_object_response,
    object_item,
    string_field,
)
from api_tests.client import GatewayClient
from api_tests.openapi import JsonValue


@pytest.mark.openapi("getExchangeRates")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_exchange_rates_returns_rates_or_documented_business_error(
    gateway_client: GatewayClient,
) -> None:
    target_currency = _first_enabled_target_currency(gateway_client)

    response = gateway_client.api_request(
        "GET",
        "/v1/exchange-rates",
        params={
            "targetCurrency": target_currency,
            "startDate": "2025-01-01",
            "endDate": "2025-01-31",
        },
    )

    assert_status_in(response, {httpx.codes.OK, httpx.codes.UNPROCESSABLE_ENTITY})
    if response.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        error = json_object_response(response, httpx.codes.UNPROCESSABLE_ENTITY)
        string_field(error, "type")
        string_field(error, "message")
        return

    rates = json_array_response(response, httpx.codes.OK)
    for item in rates:
        rate = object_item(item)
        assert string_field(rate, "baseCurrency") == "USD"
        assert string_field(rate, "targetCurrency") == target_currency
        string_field(rate, "date")
        string_field(rate, "publishedDate")


def _first_enabled_target_currency(gateway_client: GatewayClient) -> str:
    response = gateway_client.api_request("GET", "/v1/currencies", params={"enabledOnly": True})
    currencies = json_array_response(response, httpx.codes.OK)
    for item in currencies:
        currency = object_item(item)
        currency_code = string_field(currency, "currencyCode")
        if currency_code != "USD":
            return currency_code

    return _fallback_target_currency(currencies)


def _fallback_target_currency(currencies: list[JsonValue]) -> str:
    if currencies:
        return string_field(object_item(currencies[0]), "currencyCode")
    return "THB"
