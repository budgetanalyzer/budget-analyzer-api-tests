from __future__ import annotations

import pytest
from pydantic import ValidationError

from api_tests.config import EnvironmentConfig, load_environment


@pytest.mark.parametrize("environment_name", ["local", "staging", "production"])
def test_committed_environment_config_loads(environment_name: str) -> None:
    config = load_environment(environment_name)

    assert config.name == environment_name
    if config.environment_type == "production":
        assert config.allow_mutation is False
        assert config.allow_destructive is False
        assert config.auth.mode == "supplied_sessions"
        assert config.auth.supplied_sessions.secondary_session_env is None
        assert config.auth.browser is None
        assert config.data.per_run_user_boundary is False


def test_env_config_fixture_exposes_selected_environment(env_config: EnvironmentConfig) -> None:
    assert env_config.name
    assert env_config.origin


def test_production_rejects_browser_session_mode() -> None:
    config = load_environment("production").model_dump()
    auth_config = config["auth"]
    assert isinstance(auth_config, dict)
    auth_config["mode"] = "browser_preprovisioned"

    with pytest.raises(ValidationError, match=r"production auth\.mode must use supplied_sessions"):
        EnvironmentConfig.model_validate(config)


def test_production_rejects_per_run_user_boundary() -> None:
    config = load_environment("production").model_dump()
    data_config = config["data"]
    assert isinstance(data_config, dict)
    data_config["per_run_user_boundary"] = True

    with pytest.raises(ValidationError, match="must not create per-run users"):
        EnvironmentConfig.model_validate(config)


def test_production_rejects_browser_settings() -> None:
    config = load_environment("production").model_dump()
    auth_config = config["auth"]
    assert isinstance(auth_config, dict)
    auth_config["browser"] = {
        "headless": True,
        "primary_username_env": "BA_PRIMARY_TEST_USERNAME",
        "primary_password_env": "BA_PRIMARY_TEST_PASSWORD",
        "secondary_username_env": "BA_SECONDARY_TEST_USERNAME",
        "secondary_password_env": "BA_SECONDARY_TEST_PASSWORD",
    }

    with pytest.raises(ValidationError, match="must not configure browser acquisition"):
        EnvironmentConfig.model_validate(config)
