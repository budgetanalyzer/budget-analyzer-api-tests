from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import ValidationError

from api_tests.client import GatewayClient
from api_tests.config import EnvironmentConfig, load_environment, repository_root
from api_tests.coverage import (
    OperationCoverage,
    missing_operation_ids,
    placeholder_operation_ids,
    write_coverage_artifact,
)
from api_tests.dependency_inventory import DependencyInventoryError, write_dependency_inventory
from api_tests.local_credentials import LocalCredentialError, ensure_exact_local_target
from api_tests.openapi import fetch_live_openapi, load_snapshot
from api_tests.openapi_drift import compare_openapi_documents, write_drift_artifact
from api_tests.session_gateway_inventory import (
    build_session_gateway_coverage_report,
    session_gateway_coverage_lines,
    write_session_gateway_coverage_artifact,
)
from api_tests.tools.openapi_coverage import build_coverage, build_session_gateway_coverage


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if any(argument == "--env" or argument.startswith("--env=") for argument in arguments):
        print(
            "run-tests always uses --env local; pass environment-changing options to pytest "
            "directly when working outside the supported local workflow",
            file=sys.stderr,
        )
        return 2

    try:
        config = load_environment("local")
        ensure_exact_local_target(config)
    except (FileNotFoundError, ValueError, LocalCredentialError) as exc:
        print(f"run-tests local target validation failed: {exc}", file=sys.stderr)
        return 2

    artifact_dir = _new_artifact_dir(repository_root() / "artifacts" / "runs")
    print(f"run artifacts: {artifact_dir}")
    if arguments:
        print(
            "PARTIAL VALIDATION: pytest selectors were supplied; coverage and live contract "
            "drift gates are not included."
        )
        return _run_pytest(arguments, artifact_dir)

    return _run_full_validation(config, artifact_dir)


def _run_full_validation(config: EnvironmentConfig, artifact_dir: Path) -> int:
    print(
        "FULL LOCAL REGRESSION: checking dependency inventory, coverage, and live contract drift."
    )
    try:
        write_dependency_inventory(
            artifact_dir / "dependency-inventory.json",
            root=repository_root(),
            python_executable=Path(sys.executable),
        )
        coverage = build_coverage(env_name="local", source="snapshot")
        write_coverage_artifact(artifact_dir / "openapi-coverage.json", coverage)
        session_gateway_coverage = build_session_gateway_coverage()
        session_gateway_operations = tuple(
            row for row in coverage if row.contract == "session_gateway"
        )
        session_gateway_report = build_session_gateway_coverage_report(
            session_gateway_operations, session_gateway_coverage
        )
        write_session_gateway_coverage_artifact(
            artifact_dir / "session-gateway-coverage.json", session_gateway_report
        )
        missing = missing_operation_ids(coverage)
        placeholders = placeholder_operation_ids(coverage)
        uncovered_cases = session_gateway_report.claims.uncovered_executable_case_ids
        if missing or placeholders or uncovered_cases:
            if missing:
                print(f"missing OpenAPI markers: {', '.join(missing)}", file=sys.stderr)
            if placeholders:
                print(
                    f"placeholder OpenAPI coverage remains: {', '.join(placeholders)}",
                    file=sys.stderr,
                )
            if uncovered_cases:
                print(
                    "Session Gateway executable cases missing markers: "
                    + ", ".join(uncovered_cases),
                    file=sys.stderr,
                )
            return 1

        for line in session_gateway_coverage_lines(session_gateway_report):
            print(line)
        print(_unified_coverage_summary(coverage))
        print("Session Gateway reviewed contract and inbound inventory validation passed.")

        snapshot_path = repository_root() / "schemas" / "openapi.json"
        request_log_path = artifact_dir / "request-log.jsonl"
        with GatewayClient(config, artifact_path=request_log_path) as client:
            live_document = fetch_live_openapi(client, config.openapi_path)
        differences = compare_openapi_documents(load_snapshot(snapshot_path), live_document)
        write_drift_artifact(
            artifact_dir / "openapi-drift.json",
            differences,
            baseline_path=str(snapshot_path.relative_to(repository_root())),
            live_url=f"{config.origin}{config.openapi_path}",
        )
    except (
        FileNotFoundError,
        DependencyInventoryError,
        LocalCredentialError,
        ValueError,
        ValidationError,
        httpx.RequestError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"full local regression setup failed: {exc}", file=sys.stderr)
        return 1

    if differences:
        print(
            f"Unified live OpenAPI contract drift: {len(differences)} difference(s).",
            file=sys.stderr,
        )
        print("Live unified contract differs from schemas/openapi.json:", file=sys.stderr)
        for difference in differences:
            detail = ""
            if difference.baseline is not None or difference.live is not None:
                detail = f" (baseline={difference.baseline!r}, live={difference.live!r})"
            print(
                f"- {difference.change}: {difference.location}{detail}",
                file=sys.stderr,
            )
        print(
            "review the public contract change explicitly; refresh the snapshot separately "
            "with tools/refresh-openapi.py only after acceptance",
            file=sys.stderr,
        )
        return 1

    print("Unified live OpenAPI contract drift: none; contract matches schemas/openapi.json.")
    return _run_pytest([], artifact_dir)


def _unified_coverage_summary(coverage: Sequence[OperationCoverage]) -> str:
    unified = tuple(row for row in coverage if row.contract == "unified")
    covered = sum(row.status == "covered" for row in unified)
    deferred_admin = sum(row.deferred_admin for row in unified)
    return (
        "Unified OpenAPI operation coverage passed: "
        f"{len(unified)} operations ({covered} covered, {deferred_admin} deferred admin)."
    )


def _run_pytest(arguments: Sequence[str], artifact_dir: Path) -> int:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "api_tests.pytest_plugin",
            "--env",
            "local",
            "--request-log",
            str(artifact_dir / "request-log.jsonl"),
            "--junitxml",
            str(artifact_dir / "pytest.xml"),
            *arguments,
        ],
        check=False,
    )
    return result.returncode


def _new_artifact_dir(parent: Path) -> Path:
    now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{now}-{uuid4().hex[:8]}"
    path = parent / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


if __name__ == "__main__":
    raise SystemExit(main())
