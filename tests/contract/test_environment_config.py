from __future__ import annotations

import pytest

from api_tests.config import EnvironmentConfig, load_environment


@pytest.mark.parametrize("environment_name", ["local", "staging", "production"])
def test_committed_environment_config_loads(environment_name: str) -> None:
    config = load_environment(environment_name)

    assert config.name == environment_name
    if config.environment_type == "production":
        assert config.allow_mutation is False
        assert config.allow_destructive is False


def test_env_config_fixture_exposes_selected_environment(env_config: EnvironmentConfig) -> None:
    assert env_config.name
    assert env_config.origin
