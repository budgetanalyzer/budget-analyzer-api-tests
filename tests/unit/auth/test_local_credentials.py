from __future__ import annotations

import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from api_tests.auth import (
    AuthConfigurationError,
    acquire_browser_session,
    acquire_disposable_browser_target,
)
from api_tests.browser_login import (
    BrowserAccountExistsError,
    BrowserLoginCredentials,
    BrowserLoginError,
)
from api_tests.config import EnvironmentConfig, load_environment, with_session_mode
from api_tests.local_credentials import (
    DisposableCredentialRecord,
    LocalCredentialError,
    bind_disposable_identity,
    load_or_create_disposable_credentials,
    load_or_create_local_credentials,
    mark_disposable_credentials_complete,
    require_owned_disposable_identity,
)
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
    assert "BA_ADMIN_TEST_USERNAME" not in saved
    assert "BA_ADMIN_TEST_PASSWORD" not in saved


def test_local_roles_cannot_reuse_the_same_saved_username(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    credential_path.write_text(
        "BA_PRIMARY_TEST_USERNAME=reused@test.com\n"
        "BA_PRIMARY_TEST_PASSWORD=primary-password\n"
        "BA_SECONDARY_TEST_USERNAME=reused@test.com\n"
        "BA_SECONDARY_TEST_PASSWORD=secondary-password\n",
        encoding="utf-8",
    )

    with pytest.raises(LocalCredentialError, match="distinct identities"):
        load_or_create_local_credentials(
            config,
            "secondary",
            environ={},
            path=credential_path,
        )


def test_admin_creation_uses_its_own_lazy_credential_slot(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"

    acquire_browser_session(
        config,
        "admin",
        environ={},
        signup=lambda environment_config, credentials: _session(),
        login=_unexpected_flow("login"),
        credential_path=credential_path,
        credential_generator=_credentials,
    )

    saved = credential_path.read_text(encoding="utf-8")
    assert "BA_ADMIN_TEST_USERNAME=" in saved
    assert "BA_ADMIN_TEST_PASSWORD=" in saved
    assert "BA_ADMIN_TEST_ACCOUNT_STATE=complete" in saved
    assert "BA_PRIMARY_TEST_USERNAME" not in saved
    assert "BA_SECONDARY_TEST_USERNAME" not in saved


def test_admin_partial_pair_is_preserved_and_rejected(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    original = "BA_ADMIN_TEST_USERNAME=preserve-admin@test.com\n"
    credential_path.write_text(original, encoding="utf-8")

    with pytest.raises(AuthConfigurationError, match="partial admin credentials"):
        acquire_browser_session(
            config,
            "admin",
            environ={},
            login=_unexpected_flow("login"),
            signup=_unexpected_flow("signup"),
            credential_path=credential_path,
            credential_generator=_unexpected_generator,
        )

    assert credential_path.read_text(encoding="utf-8") == original


def test_admin_pending_signup_recovers_with_the_same_credentials(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    credential_path.write_text(
        "BA_ADMIN_TEST_USERNAME=pending-admin@test.com\n"
        "BA_ADMIN_TEST_PASSWORD=pending-password\n"
        "BA_ADMIN_TEST_ACCOUNT_STATE=pending\n",
        encoding="utf-8",
    )
    observed: list[BrowserLoginCredentials] = []

    def account_exists(
        environment_config: EnvironmentConfig,
        supplied: BrowserLoginCredentials,
    ) -> Session:
        del environment_config
        observed.append(supplied)
        raise BrowserAccountExistsError("saved admin account already exists")

    session = acquire_browser_session(
        config,
        "admin",
        environ={},
        signup=account_exists,
        login=lambda environment_config, credentials: _session(),
        credential_path=credential_path,
        credential_generator=_unexpected_generator,
    )

    assert session == _session()
    assert observed == [BrowserLoginCredentials("pending-admin@test.com", "pending-password")]
    assert "BA_ADMIN_TEST_ACCOUNT_STATE=complete" in credential_path.read_text(encoding="utf-8")


def test_admin_cannot_reuse_a_primary_saved_username(tmp_path: Path) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    credential_path.write_text(
        "BA_PRIMARY_TEST_USERNAME=reused@test.com\n"
        "BA_PRIMARY_TEST_PASSWORD=primary-password\n"
        "BA_ADMIN_TEST_USERNAME=reused@test.com\n"
        "BA_ADMIN_TEST_PASSWORD=admin-password\n",
        encoding="utf-8",
    )

    with pytest.raises(LocalCredentialError, match="admin and primary"):
        load_or_create_local_credentials(
            config,
            "admin",
            environ={},
            path=credential_path,
        )


def test_disposable_signup_is_saved_before_use_and_retained_per_execution(
    tmp_path: Path,
) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    first = BrowserLoginCredentials("disposable-one@test.com", "first-secret-password")
    second = BrowserLoginCredentials("disposable-two@test.com", "second-secret-password")

    first_record = load_or_create_disposable_credentials(
        config,
        "run-one",
        "deactivation",
        path=credential_path,
        generator=lambda: first,
    )
    second_record = load_or_create_disposable_credentials(
        config,
        "run-two",
        "deactivation",
        path=credential_path,
        generator=lambda: second,
    )

    saved = credential_path.read_text(encoding="utf-8")
    assert first_record.credentials == first
    assert second_record.credentials == second
    assert saved.count("_ACCOUNT_STATE=pending") == 2
    assert "RUN_ONE_DEACTIVATION_USERNAME=disposable-one@test.com" in saved
    assert "RUN_TWO_DEACTIVATION_USERNAME=disposable-two@test.com" in saved
    assert stat.S_IMODE(credential_path.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR
    assert first.username not in repr(first_record)
    assert first.password not in repr(first_record)


def test_interrupted_disposable_signup_reuses_current_record_and_credentials(
    tmp_path: Path,
) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    credentials = BrowserLoginCredentials("interrupted-target@test.com", "retained-password")
    generated = 0
    signup_credentials: list[BrowserLoginCredentials] = []
    login_credentials: list[BrowserLoginCredentials] = []

    def generate() -> BrowserLoginCredentials:
        nonlocal generated
        generated += 1
        return credentials

    def interrupted_signup(
        environment_config: EnvironmentConfig,
        supplied: BrowserLoginCredentials,
    ) -> Session:
        assert environment_config is config
        signup_credentials.append(supplied)
        raise BrowserLoginError("signup callback interrupted")

    with pytest.raises(BrowserLoginError, match="interrupted"):
        acquire_disposable_browser_target(
            config,
            "same-run",
            "deactivation",
            signup=interrupted_signup,
            login=_unexpected_flow("login"),
            credential_path=credential_path,
            credential_generator=generate,
        )

    def existing_account(
        environment_config: EnvironmentConfig,
        supplied: BrowserLoginCredentials,
    ) -> Session:
        assert environment_config is config
        signup_credentials.append(supplied)
        raise BrowserAccountExistsError("account already exists")

    def login(
        environment_config: EnvironmentConfig,
        supplied: BrowserLoginCredentials,
    ) -> Session:
        assert environment_config is config
        login_credentials.append(supplied)
        return _session()

    target = acquire_disposable_browser_target(
        config,
        "same-run",
        "deactivation",
        signup=existing_account,
        login=login,
        credential_path=credential_path,
        credential_generator=generate,
    )

    assert generated == 1
    assert signup_credentials == [credentials, credentials]
    assert login_credentials == [credentials]
    assert target.first_session == _session()
    assert target.record.state == "complete"
    assert "_ACCOUNT_STATE=complete" in credential_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (
            with_session_mode(load_environment("local"), "supplied_sessions"),
            "exact-local browser_preprovisioned",
        ),
        (load_environment("staging"), "automatic local credentials require"),
        (load_environment("production"), "exact-local browser_preprovisioned"),
    ],
    ids=["supplied-session", "staging", "production"],
)
def test_disposable_provisioning_rejects_unsupported_modes_before_file_access(
    config: EnvironmentConfig,
    message: str,
    tmp_path: Path,
) -> None:
    credential_path = tmp_path / ".env.local"

    with pytest.raises(AuthConfigurationError, match=message):
        acquire_disposable_browser_target(
            config,
            "run",
            "deactivation",
            credential_path=credential_path,
            credential_generator=_unexpected_generator,
        )

    assert not credential_path.exists()


def test_disposable_provisioning_requires_mutation_and_destructive_policy(
    tmp_path: Path,
) -> None:
    config = load_environment("local").model_copy(update={"allow_destructive": False})
    credential_path = tmp_path / ".env.local"

    with pytest.raises(AuthConfigurationError, match="both mutation and destructive"):
        acquire_disposable_browser_target(
            config,
            "run",
            "deactivation",
            credential_path=credential_path,
            credential_generator=_unexpected_generator,
        )

    assert not credential_path.exists()


def test_disposable_identity_binding_rejects_reusable_and_duplicate_identities(
    tmp_path: Path,
) -> None:
    config = load_environment("local")
    credential_path = tmp_path / ".env.local"
    first = mark_disposable_credentials_complete(
        config,
        load_or_create_disposable_credentials(
            config,
            "run-one",
            "deactivation",
            path=credential_path,
            generator=lambda: BrowserLoginCredentials("one@test.com", "one-password"),
        ),
        path=credential_path,
    )
    second = mark_disposable_credentials_complete(
        config,
        load_or_create_disposable_credentials(
            config,
            "run-two",
            "deactivation",
            path=credential_path,
            generator=lambda: BrowserLoginCredentials("two@test.com", "two-password"),
        ),
        path=credential_path,
    )

    with pytest.raises(LocalCredentialError, match="reusable identity"):
        bind_disposable_identity(
            config,
            first,
            current_run_id="run-one",
            current_scenario_id="deactivation",
            subject="idp|primary",
            user_id="usr-primary",
            reusable_subjects=frozenset({"idp|primary"}),
            reusable_user_ids=frozenset({"usr-primary"}),
            path=credential_path,
        )

    bound = bind_disposable_identity(
        config,
        first,
        current_run_id="run-one",
        current_scenario_id="deactivation",
        subject="idp|target",
        user_id="usr-target",
        reusable_subjects=frozenset(),
        reusable_user_ids=frozenset(),
        path=credential_path,
    )
    with pytest.raises(LocalCredentialError, match="already bound to another execution"):
        bind_disposable_identity(
            config,
            second,
            current_run_id="run-two",
            current_scenario_id="deactivation",
            subject="idp|target",
            user_id="usr-other",
            reusable_subjects=frozenset(),
            reusable_user_ids=frozenset(),
            path=credential_path,
        )
    with pytest.raises(LocalCredentialError, match="current run and scenario"):
        require_owned_disposable_identity(
            bound,
            current_run_id="wrong-run",
            current_scenario_id="deactivation",
            subject="idp|target",
            user_id="usr-target",
            reusable_subjects=frozenset(),
            reusable_user_ids=frozenset(),
        )


def test_disposable_ownership_requires_completed_exact_binding() -> None:
    credentials = BrowserLoginCredentials("target@test.com", "target-password")
    record = DisposableCredentialRecord("run", "deactivation", credentials, "pending")

    with pytest.raises(LocalCredentialError, match="completed identity binding"):
        require_owned_disposable_identity(
            record,
            current_run_id="run",
            current_scenario_id="deactivation",
            subject="idp|target",
            user_id="usr-target",
            reusable_subjects=frozenset(),
            reusable_user_ids=frozenset(),
        )


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
