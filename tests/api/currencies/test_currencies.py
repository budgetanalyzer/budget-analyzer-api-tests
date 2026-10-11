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
from api_tests.openapi import OpenApiDocument
from api_tests.schemas import assert_response_matches_openapi


@pytest.mark.openapi("getAll")
@pytest.mark.openapi("getById")
@pytest.mark.readonly
@pytest.mark.production_safe
def test_currency_catalog_lists_series_and_reads_one_by_id(
    gateway_client: GatewayClient,
    openapi_snapshot: OpenApiDocument,
) -> None:
    response = gateway_client.api_request("GET", "/v1/currencies", params={"enabledOnly": False})

    assert_response_matches_openapi(response, "getAll", httpx.codes.OK, openapi_snapshot)
    currencies = json_array_response(response, httpx.codes.OK)
    assert currencies, "expected at least one configured currency series"
    for item in currencies:
        currency = object_item(item)
        int_field(currency, "id")
        string_field(currency, "currencyCode")
        string_field(currency, "providerSeriesId")
        bool_field(currency, "enabled")
    currency_id = int_field(object_item(currencies[0]), "id")

    response = gateway_client.api_request("GET", f"/v1/currencies/{currency_id}")

    assert_response_matches_openapi(response, "getById", httpx.codes.OK, openapi_snapshot)
    currency = json_object_response(response, httpx.codes.OK)
    assert int_field(currency, "id") == currency_id
    string_field(currency, "currencyCode")
    string_field(currency, "providerSeriesId")
