from __future__ import annotations

import warnings

import httpx
import pytest

from api_tests.assertions import (
    bool_field,
    json_array_response,
    object_item,
    string_field,
)
from api_tests.client import GatewayClient
from api_tests.openapi import OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi


@pytest.mark.openapi("getExchangeRates")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_get_exchange_rates_returns_rates(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    target_currency = _first_enabled_target_currency(gateway_client, openapi_snapshot)

    response = gateway_client.api_request(
        "GET",
        "/v1/exchange-rates",
        params={
            "targetCurrency": target_currency,
            "startDate": "2025-01-01",
            "endDate": "2025-01-31",
        },
    )

    assert_response_matches_openapi(response, "getExchangeRates", httpx.codes.OK, openapi_snapshot)
    rates = json_array_response(response, httpx.codes.OK)
    assert rates, f"expected January 2025 exchange rates for {target_currency}"
    for item in rates:
        rate = object_item(item)
        assert string_field(rate, "baseCurrency") == "USD"
        assert string_field(rate, "targetCurrency") == target_currency
        date = string_field(rate, "date")
        assert "2025-01-01" <= date <= "2025-01-31"
        string_field(rate, "publishedDate")
        value = rate.get("rate")
        assert isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _first_enabled_target_currency(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> str:
    response = gateway_client.api_request("GET", "/v1/currencies", params={"enabledOnly": False})
    assert_response_matches_openapi(response, "getAll", httpx.codes.OK, openapi_snapshot)
    currencies = json_array_response(response, httpx.codes.OK)
    assert currencies, "expected at least one configured currency series"
    enabled_codes: list[str] = []
    for item in currencies:
        currency = object_item(item)
        if bool_field(currency, "enabled"):
            enabled_codes.append(string_field(currency, "currencyCode"))

    if not enabled_codes:
        reason = (
            "successful getExchangeRates path not tested: no configured currency series is enabled"
        )
        warnings.warn(reason, RuntimeWarning, stacklevel=2)
        pytest.skip(reason)

    for currency_code in enabled_codes:
        if currency_code != "USD":
            return currency_code
    return enabled_codes[0]
