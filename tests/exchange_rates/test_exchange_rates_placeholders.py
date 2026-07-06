from __future__ import annotations

import pytest


@pytest.mark.openapi("getExchangeRates")
@pytest.mark.placeholder
@pytest.mark.readonly
def test_get_exchange_rates_placeholder() -> None:
    pytest.skip("not implemented")
