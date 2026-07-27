from __future__ import annotations

import json
from pathlib import Path
from typing import Self

import pytest

from api_tests.coverage import OperationCoverage
from api_tests.openapi import OpenApiDocument, load_snapshot
from api_tests.prerequisites import (
    PrerequisiteCategory,
    PrerequisiteExitCode,
    PrerequisiteResult,
    PrerequisiteScope,
)
from api_tests.tools import openapi_coverage
from api_tests.tools.openapi_coverage import check_main, export_main


def test_export_openapi_coverage_writes_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "openapi-coverage.json"

    exit_code = export_main(["--env", "local", "--output", str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "wrote OpenAPI coverage artifact:" in captured.out

    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(rows, list)
    assert rows
    assert {
        "operation_id",
        "method",
        "path",
        "source",
        "deferred_admin",
        "marker_count",
        "placeholder_marker_count",
        "status",
    }.issubset(rows[0])


def test_check_openapi_coverage_fails_placeholder_gate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "openapi-coverage.json"

    def fake_build_coverage(
        *,
        env_name: str,
        source: openapi_coverage.CoverageDocumentSource,
    ) -> tuple[OperationCoverage, ...]:
        assert env_name == "local"
        assert source == "snapshot"
        return (
            OperationCoverage(
                operation_id="placeholderOperation",
                method="GET",
                path="/v1/placeholder",
                source="snapshot",
                deferred_admin=False,
                marker_count=1,
                placeholder_marker_count=1,
                status="placeholder",
            ),
        )

    monkeypatch.setattr(openapi_coverage, "build_coverage", fake_build_coverage)

    exit_code = check_main(["--env", "local", "--output", str(output_path), "--fail-placeholder"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "placeholder OpenAPI coverage remains:" in captured.err


def test_check_openapi_coverage_fails_missing_for_invalid_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = check_main(["--env", "does-not-exist", "--fail-missing"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "OpenAPI coverage failed: environment file not found:" in captured.err


def test_live_coverage_runs_public_preflight_before_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def fake_preflight(*args: object, **kwargs: object) -> PrerequisiteResult:
        del args
        assert kwargs["environment_name"] == "local"
        assert kwargs["scope"] == PrerequisiteScope.PUBLIC
        events.append("preflight")
        return PrerequisiteResult(
            category=PrerequisiteCategory.READY,
            exit_code=PrerequisiteExitCode.OK,
            message="ready",
        )

    class FakeGatewayClient:
        def __init__(self, config: object) -> None:
            del config

        def __enter__(self) -> Self:
            events.append("client")
            return self

        def __exit__(self, *args: object) -> None:
            del args

    def fake_fetch(client: object, openapi_path: str) -> OpenApiDocument:
        assert isinstance(client, FakeGatewayClient)
        assert openapi_path == "/api-docs/openapi.json"
        events.append("fetch")
        return load_snapshot(Path("schemas/openapi.json"))

    monkeypatch.setattr(openapi_coverage, "run_live_preflight", fake_preflight)
    monkeypatch.setattr(openapi_coverage, "GatewayClient", FakeGatewayClient)
    monkeypatch.setattr(openapi_coverage, "fetch_live_openapi", fake_fetch)

    coverage = openapi_coverage.build_coverage(env_name="local", source="live")

    assert coverage
    assert events[:3] == ["preflight", "client", "fetch"]


def test_refresh_stops_when_public_preflight_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failed_preflight(*args: object, **kwargs: object) -> PrerequisiteResult:
        del args, kwargs
        return PrerequisiteResult(
            category=PrerequisiteCategory.TLS,
            exit_code=PrerequisiteExitCode.TLS,
            message="certificate trust-chain validation failed",
        )

    monkeypatch.setattr(openapi_coverage, "run_live_preflight", failed_preflight)

    exit_code = openapi_coverage.refresh_main(
        ["--env", "local", "--output", str(tmp_path / "openapi.json")]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "live prerequisite failed [tls]" in captured.err
