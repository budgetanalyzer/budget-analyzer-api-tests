from __future__ import annotations

import pytest


@pytest.mark.openapi("listViews")
@pytest.mark.placeholder
@pytest.mark.readonly
def test_list_views_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("createView")
@pytest.mark.placeholder
@pytest.mark.mutation
def test_create_view_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("getView")
@pytest.mark.placeholder
@pytest.mark.readonly
def test_get_view_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("updateView")
@pytest.mark.placeholder
@pytest.mark.mutation
def test_update_view_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("deleteView")
@pytest.mark.placeholder
@pytest.mark.destructive
@pytest.mark.mutation
def test_delete_view_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("getViewTransactions")
@pytest.mark.placeholder
@pytest.mark.readonly
def test_get_view_transactions_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("pinTransaction")
@pytest.mark.placeholder
@pytest.mark.mutation
def test_pin_transaction_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("unpinTransaction")
@pytest.mark.placeholder
@pytest.mark.mutation
def test_unpin_transaction_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("bulkPinTransactions")
@pytest.mark.placeholder
@pytest.mark.mutation
def test_bulk_pin_transactions_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("excludeTransaction")
@pytest.mark.placeholder
@pytest.mark.mutation
def test_exclude_transaction_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("unexcludeTransaction")
@pytest.mark.placeholder
@pytest.mark.mutation
def test_unexclude_transaction_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("bulkExcludeTransactions")
@pytest.mark.placeholder
@pytest.mark.mutation
def test_bulk_exclude_transactions_placeholder() -> None:
    pytest.skip("not implemented")
