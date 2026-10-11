from __future__ import annotations

import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from api_tests.auth import AuthConfigurationError, acquire_browser_session
from api_tests.browser_login import (
    BrowserAccountExistsError,
    BrowserLoginCredentials,
    BrowserLoginError,
)
from api_tests.config import EnvironmentConfig, load_environment
from api_tests.local_credentials import LocalCredentialError, load_or_create_local_credentials
from api_tests.session import Session


def _credentials() -> BrowserLoginCredentials:
    return BrowserLoginCredentials(
        username="ba-api-test-fixed@test.com",
        password="Ba9!fixed-password-for-tests",
    )


def _session() -> Session:
    return Session(cookie_name="BA_SESSION", cookie_value="session-cookie")


def test_missing_file_is_saved_before_signup_and_marked_complete(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    credentials = _credentials()

    def signup(
        environment_config: EnvironmentConfig,
        supplied: BrowserLoginCredentials,
    ) -> Session:
        assert environment_config is config
        assert supplied == credentials
        saved = credential_path.read_text(encoding="utf-8")
        assert "BA_PRIMARY_TEST_ACCOUNT_STATE=pending" in saved
        assert credentials.username in saved
        assert credentials.password in saved
        return _session()

    session = acquire_browser_session(
        config,
        "primary",
        environ={},
        signup=signup,
        login=_unexpected_flow("login"),
        credential_path=credential_path,
        credential_generator=lambda: credentials,
    )

    assert session == _session()
    saved = credential_path.read_text(encoding="utf-8")
    assert "BA_PRIMARY_TEST_ACCOUNT_STATE=complete" in saved
    assert stat.S_IMODE(credential_path.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR


def test_existing_file_logs_in_and_preserves_unrelated_content(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    credential_path.write_text(
        "# keep this comment\n"
        "UNRELATED=value\n"
        "BA_PRIMARY_TEST_USERNAME=existing@test.com\n"
        "BA_PRIMARY_TEST_PASSWORD=existing-password\n"
        "BA_PRIMARY_TEST_ACCOUNT_STATE=complete\n",
        encoding="utf-8",
    )

    observed: list[BrowserLoginCredentials] = []

    def login(
        environment_config: EnvironmentConfig,
        credentials: BrowserLoginCredentials,
    ) -> Session:
        assert environment_config is config
        observed.append(credentials)
        return _session()

    acquire_browser_session(
        config,
        "primary",
        environ={},
        login=login,
        signup=_unexpected_flow("signup"),
        credential_path=credential_path,
    )

    assert observed == [BrowserLoginCredentials("existing@test.com", "existing-password")]
    assert "# keep this comment\nUNRELATED=value\n" in credential_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(credential_path.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR


def test_partial_file_is_not_replaced_or_used_for_signup(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    original = "UNRELATED=keep\nBA_PRIMARY_TEST_USERNAME=preserve@test.com\n"
    credential_path.write_text(original, encoding="utf-8")

    with pytest.raises(AuthConfigurationError, match="partial primary credentials"):
        acquire_browser_session(
            config,
            "primary",
            environ={},
            login=_unexpected_flow("login"),
            signup=_unexpected_flow("signup"),
            credential_path=credential_path,
            credential_generator=_unexpected_generator,
        )

    assert credential_path.read_text(encoding="utf-8") == original


def test_interrupted_signup_reuses_saved_identity_and_recovers_by_login(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    credentials = _credentials()

    def interrupted_signup(
        environment_config: EnvironmentConfig,
        supplied: BrowserLoginCredentials,
    ) -> Session:
        assert environment_config is config
        assert supplied == credentials
        raise BrowserLoginError("signup callback did not complete")

    with pytest.raises(BrowserLoginError, match="did not complete"):
        acquire_browser_session(
            config,
            "primary",
            environ={},
            signup=interrupted_signup,
            login=_unexpected_flow("login"),
            credential_path=credential_path,
            credential_generator=lambda: credentials,
        )

    assert "BA_PRIMARY_TEST_ACCOUNT_STATE=pending" in credential_path.read_text(encoding="utf-8")

    def account_exists(
        environment_config: EnvironmentConfig,
        supplied: BrowserLoginCredentials,
    ) -> Session:
        assert environment_config is config
        assert supplied == credentials
        raise BrowserAccountExistsError("saved account already exists")

    observed_login: list[BrowserLoginCredentials] = []

    def login(
        environment_config: EnvironmentConfig,
        supplied: BrowserLoginCredentials,
    ) -> Session:
        assert environment_config is config
        observed_login.append(supplied)
        return _session()

    session = acquire_browser_session(
        config,
        "primary",
        environ={},
        signup=account_exists,
        login=login,
        credential_path=credential_path,
        credential_generator=_unexpected_generator,
    )

    assert session == _session()
    assert observed_login == [credentials]
    assert "BA_PRIMARY_TEST_ACCOUNT_STATE=complete" in credential_path.read_text(encoding="utf-8")


def test_failed_login_preserves_existing_credentials(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    original = (
        "BA_PRIMARY_TEST_USERNAME=existing@test.com\n"
        "BA_PRIMARY_TEST_PASSWORD=wrong-password\n"
        "BA_PRIMARY_TEST_ACCOUNT_STATE=complete\n"
    )
    credential_path.write_text(original, encoding="utf-8")

    def failed_login(
        environment_config: EnvironmentConfig,
        credentials: BrowserLoginCredentials,
    ) -> Session:
        del environment_config, credentials
        raise BrowserLoginError("hosted login rejected the saved account")

    with pytest.raises(BrowserLoginError, match="rejected"):
        acquire_browser_session(
            config,
            "primary",
            environ={},
            login=failed_login,
            signup=_unexpected_flow("signup"),
            credential_path=credential_path,
            credential_generator=_unexpected_generator,
        )

    assert credential_path.read_text(encoding="utf-8") == original


def test_wrong_target_is_rejected_before_local_file_or_environment_access(
    tmp_path: Path,
) -> None:
    config = load_environment("local").model_copy(update={"origin": "https://wrong.example.test"})
    credential_path = tmp_path / ".env.local"
    credential_path.write_text("BA_PRIMARY_TEST_USERNAME=partial@test.com\n", encoding="utf-8")

    with pytest.raises(LocalCredentialError, match="resolving exactly"):
        load_or_create_local_credentials(
            config,
            "primary",
            environ={"BA_PRIMARY_TEST_PASSWORD": "must-not-be-read"},
            path=credential_path,
            generator=_unexpected_generator,
        )


def test_partial_environment_pair_does_not_mix_with_saved_pair(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    credential_path.write_text(
        "BA_PRIMARY_TEST_USERNAME=saved@test.com\nBA_PRIMARY_TEST_PASSWORD=saved-password\n",
        encoding="utf-8",
    )

    with pytest.raises(LocalCredentialError, match="environment credentials are partial"):
        load_or_create_local_credentials(
            config,
            "primary",
            environ={"BA_PRIMARY_TEST_USERNAME": "override@test.com"},
            path=credential_path,
        )


def test_primary_creation_does_not_create_secondary_credentials(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"

    acquire_browser_session(
        config,
        "primary",
        environ={},
        signup=lambda environment_config, credentials: _session(),
        login=_unexpected_flow("login"),
        credential_path=credential_path,
        credential_generator=_credentials,
    )

    saved = credential_path.read_text(encoding="utf-8")
    assert "BA_PRIMARY_TEST_USERNAME=" in saved
    assert "BA_SECONDARY_TEST_USERNAME" not in saved
    assert "BA_SECONDARY_TEST_PASSWORD" not in saved


def _unexpected_flow(name: str) -> Callable[[EnvironmentConfig, BrowserLoginCredentials], Session]:
    def fail(
        environment_config: EnvironmentConfig,
        credentials: BrowserLoginCredentials,
    ) -> Session:
        del environment_config, credentials
        raise AssertionError(f"{name} should not run")

    return fail


def _unexpected_generator() -> BrowserLoginCredentials:
    raise AssertionError("credential generation should not run")
