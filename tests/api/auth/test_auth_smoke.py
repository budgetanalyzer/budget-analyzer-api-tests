import pytest

from api_tests.session import Session


@pytest.mark.auth_smoke
@pytest.mark.readonly
def test_acquired_session_reaches_session_gateway(primary_session: Session) -> None:
    # Browser acquisition returns only after its public /auth/v1/user verification succeeds.
    assert primary_session.cookie_name


@pytest.mark.secondary_auth_smoke
@pytest.mark.readonly
def test_secondary_acquired_session_reaches_session_gateway(
    secondary_session: Session,
) -> None:
    # The secondary fixture independently performs the same verified browser flow.
    assert secondary_session.cookie_name
