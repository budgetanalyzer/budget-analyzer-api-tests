from __future__ import annotations

import pytest


@pytest.mark.openapi("getAll")
@pytest.mark.placeholder
@pytest.mark.readonly
def test_get_all_currencies_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("getById")
@pytest.mark.placeholder
@pytest.mark.readonly
def test_get_currency_by_id_placeholder() -> None:
    pytest.skip("not implemented")
