from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from api_tests.openapi import load_snapshot
from api_tests.session_gateway_assertions import (
    REQUIRED_ADMIN_PERMISSIONS,
    AdminPromotionRequired,
    AuthenticatedIdentity,
    AuthorizationConfigurationError,
    IndistinctIdentityError,
    authenticated_identity,
    ensure_distinct_identities,
    require_admin_identity,
    require_normal_identity,
)


def _identity(
    *,
    subject: str = "idp|admin",
    roles: frozenset[str] = frozenset({"USER", "ADMIN"}),
    permissions: frozenset[str] = REQUIRED_ADMIN_PERMISSIONS,
) -> AuthenticatedIdentity:
    return AuthenticatedIdentity(subject=subject, roles=roles, permissions=permissions)


def test_valid_admin_retains_user_and_has_all_required_permissions() -> None:
    require_admin_identity(_identity())


def test_normal_user_requires_manual_admin_promotion() -> None:
    with pytest.raises(AdminPromotionRequired) as exc_info:
        require_admin_identity(_identity(roles=frozenset({"USER"}), permissions=frozenset()))

    assert exc_info.value.subject == "idp|admin"


@pytest.mark.parametrize("roles", [frozenset(), frozenset({"ADMIN"})])
def test_invalid_role_shapes_are_configuration_failures(roles: frozenset[str]) -> None:
    with pytest.raises(AuthorizationConfigurationError, match="USER"):
        require_admin_identity(_identity(roles=roles))


def test_admin_with_incomplete_seed_grants_is_a_configuration_failure() -> None:
    with pytest.raises(AuthorizationConfigurationError, match="users:write"):
        require_admin_identity(_identity(permissions=REQUIRED_ADMIN_PERMISSIONS - {"users:write"}))


def test_ordinary_principal_must_remain_a_non_admin_user() -> None:
    require_normal_identity(
        _identity(roles=frozenset({"USER"}), permissions=frozenset()),
        label="primary",
    )

    with pytest.raises(AuthorizationConfigurationError, match="unexpectedly has ADMIN"):
        require_normal_identity(_identity(), label="primary")


def test_public_subjects_are_the_identity_separation_authority() -> None:
    primary = _identity(subject="idp|primary", roles=frozenset({"USER"}))
    admin = _identity(subject="idp|admin")

    ensure_distinct_identities((("primary", primary), ("admin", admin)))

    with pytest.raises(IndistinctIdentityError, match="same public subject"):
        ensure_distinct_identities(
            (("primary", primary), ("admin", _identity(subject="idp|primary")))
        )


def test_http_or_malformed_user_response_is_not_a_promotion_stop() -> None:
    response = httpx.Response(
        httpx.codes.SERVICE_UNAVAILABLE,
        headers={"content-type": "application/json"},
        json={"type": "SERVICE_UNAVAILABLE", "message": "Service Unavailable"},
    )

    with pytest.raises(AssertionError, match="HTTP 503"):
        authenticated_identity(
            response,
            load_snapshot(Path("schemas/session-gateway-openapi.yaml")),
        )
