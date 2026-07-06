from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from api_tests.client import GatewayClient
from api_tests.config import load_environment, repository_root
from api_tests.coverage import (
    OperationCoverage,
    build_operation_coverage,
    collect_openapi_markers_from_tests,
    load_deferred_admin_operations,
    missing_operation_ids,
    placeholder_operation_ids,
    write_coverage_artifact,
)
from api_tests.openapi import fetch_live_openapi, iter_operations, load_snapshot

type CoverageDocumentSource = Literal["snapshot", "live"]


def check_main(argv: Sequence[str] | None = None) -> int:
    args = _parse_coverage_args(
        "Check OpenAPI operation marker coverage",
        include_source=True,
        argv=argv,
    )
    return _run_coverage_command(
        env_name=str(args.env),
        source=args.source,
        fail_missing=bool(args.fail_missing),
        fail_placeholder=bool(args.fail_placeholder),
        output_path=Path(args.output),
    )


def export_main(argv: Sequence[str] | None = None) -> int:
    args = _parse_coverage_args(
        "Export OpenAPI operation marker coverage",
        include_source=True,
        argv=argv,
    )
    return _run_coverage_command(
        env_name=str(args.env),
        source=args.source,
        fail_missing=bool(args.fail_missing),
        fail_placeholder=bool(args.fail_placeholder),
        output_path=Path(args.output),
    )


def _parse_coverage_args(
    description: str,
    *,
    include_source: bool,
    argv: Sequence[str] | None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--env",
        default="local",
        help="environment name to load from environments/<env>.yaml",
    )
    parser.add_argument(
        "--output",
        default=str(repository_root() / "artifacts" / "openapi-coverage.json"),
        help="coverage artifact path",
    )
    parser.add_argument(
        "--fail-missing",
        action="store_true",
        help="return non-zero when a non-deferred operation has no openapi marker",
    )
    parser.add_argument(
        "--fail-placeholder",
        action="store_true",
        help="return non-zero when a non-deferred operation only has placeholder markers",
    )
    if include_source:
        parser.add_argument(
            "--source",
            choices=("snapshot", "live"),
            default="snapshot",
            help="OpenAPI document source; live fetches the environment openapi_path",
        )
    return parser.parse_args(argv)


def _run_coverage_command(
    *,
    env_name: str,
    source: CoverageDocumentSource,
    fail_missing: bool,
    fail_placeholder: bool,
    output_path: Path,
) -> int:
    try:
        coverage = build_coverage(env_name=env_name, source=source)
        write_coverage_artifact(output_path, coverage)
    except (FileNotFoundError, ValueError, ValidationError) as exc:
        print(f"OpenAPI coverage failed: {exc}", file=sys.stderr)
        return 1

    missing = missing_operation_ids(coverage)
    placeholders = placeholder_operation_ids(coverage)
    print(f"wrote OpenAPI coverage artifact: {output_path}")
    print(f"operations: {len(coverage)}")
    print(f"missing: {len(missing)}")
    print(f"placeholder: {len(placeholders)}")
    print(
        "deferred_admin: "
        f"{sum(1 for operation in coverage if operation.status == 'deferred_admin')}"
    )

    if fail_missing and missing:
        print(f"missing OpenAPI markers: {', '.join(missing)}", file=sys.stderr)
        return 1
    if fail_placeholder and placeholders:
        print(f"placeholder OpenAPI coverage remains: {', '.join(placeholders)}", file=sys.stderr)
        return 1
    return 0


def build_coverage(
    *,
    env_name: str,
    source: CoverageDocumentSource,
) -> tuple[OperationCoverage, ...]:
    config = load_environment(env_name)
    root = repository_root()
    deferred = load_deferred_admin_operations(root / "schemas" / "deferred-admin-operations.yaml")
    markers = collect_openapi_markers_from_tests(root / "tests")

    if source == "live":
        with GatewayClient(config) as client:
            openapi_doc = fetch_live_openapi(client, config.openapi_path)
    else:
        openapi_doc = load_snapshot(root / "schemas" / "openapi.json")

    return build_operation_coverage(
        iter_operations(openapi_doc),
        deferred,
        markers,
        source=source,
    )


def refresh_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the checked-in OpenAPI snapshot")
    parser.add_argument(
        "--env",
        default="local",
        help="environment name to load from environments/<env>.yaml",
    )
    parser.add_argument(
        "--output",
        default=str(repository_root() / "schemas" / "openapi.json"),
        help="snapshot path to overwrite",
    )
    args = parser.parse_args(argv)

    try:
        config = load_environment(str(args.env))
        with GatewayClient(config) as client:
            openapi_doc = fetch_live_openapi(client, config.openapi_path)
    except (FileNotFoundError, ValueError, ValidationError) as exc:
        print(f"OpenAPI refresh failed: {exc}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.write_text(
        json.dumps(openapi_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote OpenAPI snapshot: {output_path}")
    return 0
