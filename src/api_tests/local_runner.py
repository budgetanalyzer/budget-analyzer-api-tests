from __future__ import annotations

import json
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
    missing_operation_ids,
    placeholder_operation_ids,
    write_coverage_artifact,
)
from api_tests.local_credentials import LocalCredentialError, ensure_exact_local_target
from api_tests.openapi import fetch_live_openapi, load_snapshot
from api_tests.openapi_drift import compare_openapi_documents, write_drift_artifact
from api_tests.tools.openapi_coverage import build_coverage


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
        _write_dependency_inventory(artifact_dir / "dependencies.txt")
        coverage = build_coverage(env_name="local", source="snapshot")
        write_coverage_artifact(artifact_dir / "openapi-coverage.json", coverage)
        missing = missing_operation_ids(coverage)
        placeholders = placeholder_operation_ids(coverage)
        if missing or placeholders:
            if missing:
                print(f"missing OpenAPI markers: {', '.join(missing)}", file=sys.stderr)
            if placeholders:
                print(
                    f"placeholder OpenAPI coverage remains: {', '.join(placeholders)}",
                    file=sys.stderr,
                )
            return 1

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
        LocalCredentialError,
        ValueError,
        ValidationError,
        httpx.RequestError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"full local regression setup failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"OpenAPI coverage passed: {len(coverage)} operations, "
        f"{sum(row.deferred_admin for row in coverage)} deferred admin."
    )
    if differences:
        print("live OpenAPI contract differs from schemas/openapi.json:", file=sys.stderr)
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

    print("Live OpenAPI contract matches the checked-in snapshot.")
    return _run_pytest([], artifact_dir)


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


def _write_dependency_inventory(path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--all", "--exclude-editable"],
        check=True,
        capture_output=True,
        text=True,
    )
    path.write_text(result.stdout, encoding="utf-8")
    metadata = {
        "python": sys.version,
        "executable": sys.executable,
    }
    path.with_name("runtime.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
