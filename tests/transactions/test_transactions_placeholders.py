from __future__ import annotations

import pytest


@pytest.mark.openapi("getTransactions")
@pytest.mark.placeholder
@pytest.mark.readonly
def test_get_transactions_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("searchTransactions")
@pytest.mark.placeholder
@pytest.mark.readonly
def test_search_transactions_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("countTransactions")
@pytest.mark.placeholder
@pytest.mark.readonly
def test_count_transactions_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("getTransaction")
@pytest.mark.placeholder
@pytest.mark.readonly
def test_get_transaction_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("updateTransaction")
@pytest.mark.placeholder
@pytest.mark.mutation
def test_update_transaction_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("deleteTransaction")
@pytest.mark.placeholder
@pytest.mark.destructive
@pytest.mark.mutation
def test_delete_transaction_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("previewTransactions")
@pytest.mark.placeholder
@pytest.mark.mutation
def test_preview_transactions_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("batchImportTransactions")
@pytest.mark.placeholder
@pytest.mark.destructive
@pytest.mark.mutation
def test_batch_import_transactions_placeholder() -> None:
    pytest.skip("not implemented")


@pytest.mark.openapi("bulkDeleteTransactions")
@pytest.mark.placeholder
@pytest.mark.destructive
@pytest.mark.mutation
def test_bulk_delete_transactions_placeholder() -> None:
    pytest.skip("not implemented")
