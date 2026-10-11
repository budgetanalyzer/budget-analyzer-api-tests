from __future__ import annotations

import pytest
from _pytest.pytester import Pytester
from _pytest.tmpdir import TempPathFactory


@pytest.fixture
def pytester(
    request: pytest.FixtureRequest,
    tmp_path_factory: TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Pytester:
    original_getoption = request.config.getoption

    def getoption(name: str, default: object = None, skip: bool = False) -> object:
        if name == "--runpytest":
            return "inprocess"
        return original_getoption(name, default, skip)

    monkeypatch.setattr(request.config, "getoption", getoption)
    return Pytester(request, tmp_path_factory, monkeypatch, _ispytest=True)
