from __future__ import annotations

import json
from pathlib import Path
from typing import Self

import httpx
import pytest

from api_tests.coverage import OperationCoverage
from api_tests.openapi import OpenApiDocument, load_snapshot
from api_tests.session_gateway_inventory import SessionGatewayCaseCoverage
from api_tests.tools import openapi_coverage
from api_tests.tools.openapi_coverage import check_main


def test_check_openapi_coverage_writes_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "openapi-coverage.json"
    session_gateway_output_path = tmp_path / "session-gateway-coverage.json"

    exit_code = check_main(
        [
            "--env",
            "local",
            "--output",
            str(output_path),
            "--session-gateway-output",
            str(session_gateway_output_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "wrote OpenAPI coverage artifact:" in captured.out
    assert "session_gateway_operation_covered: 3; ids:" in captured.out
    assert "session_gateway_scenario_deferred: 11; ids:" in captured.out
    assert "session_gateway_scenario_untestable: 9; ids:" in captured.out
    assert "full_session_gateway_behavior_coverage: false" in captured.out

    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(rows, list)
    assert rows
    assert {
        "operation_id",
        "contract",
        "method",
        "path",
        "source",
        "deferred_admin",
        "marker_count",
        "placeholder_marker_count",
        "status",
    }.issubset(rows[0])
    session_report = json.loads(session_gateway_output_path.read_text(encoding="utf-8"))
    assert isinstance(session_report, dict)
    assert set(session_report) == {"claims", "operation_coverage", "scenario_coverage"}
    assert session_report["claims"]["all_known_inbound_routes_inventoried"] is True
    assert session_report["claims"]["all_currently_executable_public_cases_covered"] is True
    assert session_report["claims"]["full_session_gateway_behavior_coverage"] is False
    session_rows = session_report["scenario_coverage"]["rows"]
    assert {
        "case_id",
        "status",
        "test_marker_count",
        "reason",
        "prerequisite",
        "boundary",
    }.issubset(session_rows[0])


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
                contract="unified",
                operation_id="placeholderOperation",
                method="GET",
                path="/v1/placeholder",
                source="snapshot",
                deferred_admin=False,
                marker_count=1,
                placeholder_marker_count=1,
                status="placeholder",
            ),
            _session_gateway_operation_coverage(),
        )

    monkeypatch.setattr(openapi_coverage, "build_coverage", fake_build_coverage)

    exit_code = check_main(["--env", "local", "--output", str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "placeholder OpenAPI coverage remains:" in captured.err


def test_check_openapi_coverage_fails_executable_session_gateway_case_without_marker(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_coverage(
        *,
        env_name: str,
        source: openapi_coverage.CoverageDocumentSource,
    ) -> tuple[OperationCoverage, ...]:
        assert env_name == "local"
        assert source == "snapshot"
        return (_session_gateway_operation_coverage(),)

    def fake_build_session_gateway_coverage() -> tuple[SessionGatewayCaseCoverage, ...]:
        return (
            SessionGatewayCaseCoverage(
                contract="session_gateway",
                case_id="logout-active-session",
                method="GET",
                path="/logout",
                owner="controller",
                exposure="public",
                openapi_operation_id="logout",
                behavior="redirect",
                status="covered",
                test_marker_count=0,
                reason=None,
                prerequisite=None,
                boundary=None,
                optional_indirect_proof=None,
            ),
        )

    monkeypatch.setattr(openapi_coverage, "build_coverage", fake_build_coverage)
    monkeypatch.setattr(
        openapi_coverage,
        "build_session_gateway_coverage",
        fake_build_session_gateway_coverage,
    )

    exit_code = check_main(
        [
            "--env",
            "local",
            "--output",
            str(tmp_path / "openapi.json"),
            "--session-gateway-output",
            str(tmp_path / "session-gateway.json"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Session Gateway executable cases missing markers: logout-active-session" in captured.err


def test_check_openapi_coverage_fails_missing_for_invalid_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = check_main(["--env", "does-not-exist"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "OpenAPI coverage failed: environment file not found:" in captured.err


def test_live_coverage_fetches_without_a_separate_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

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

    monkeypatch.setattr(openapi_coverage, "GatewayClient", FakeGatewayClient)
    monkeypatch.setattr(openapi_coverage, "fetch_live_openapi", fake_fetch)

    coverage = openapi_coverage.build_coverage(env_name="local", source="live")

    assert coverage
    assert events == ["client", "fetch"]


def test_refresh_reports_direct_connection_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingGatewayClient:
        def __init__(self, config: object) -> None:
            del config

        def __enter__(self) -> Self:
            raise httpx.ConnectError("certificate verification failed")

        def __exit__(self, *args: object) -> None:
            del args

    monkeypatch.setattr(openapi_coverage, "GatewayClient", FailingGatewayClient)

    exit_code = openapi_coverage.refresh_main(
        ["--env", "local", "--output", str(tmp_path / "openapi.json")]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "OpenAPI refresh failed: certificate verification failed" in captured.err


def _session_gateway_operation_coverage() -> OperationCoverage:
    return OperationCoverage(
        contract="session_gateway",
        operation_id="logout",
        method="GET",
        path="/logout",
        source="snapshot",
        deferred_admin=False,
        marker_count=1,
        placeholder_marker_count=0,
        status="covered",
    )
