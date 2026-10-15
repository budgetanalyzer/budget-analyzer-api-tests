from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import httpx
from pydantic import ValidationError

from api_tests.client import GatewayClient
from api_tests.config import load_environment, repository_root
from api_tests.coverage import (
    OperationCoverage,
    build_operation_coverage,
    collect_openapi_markers_from_tests,
    collect_session_gateway_case_markers_from_tests,
    load_deferred_admin_operations,
    missing_operation_ids,
    placeholder_operation_ids,
    write_coverage_artifact,
)
from api_tests.openapi import (
    build_operation_request_cases,
    fetch_live_openapi,
    iter_operations,
    load_forbidden_response_deferrals,
    load_snapshot,
    validate_forbidden_response_deferrals,
)
from api_tests.session_gateway_inventory import (
    SessionGatewayCaseCoverage,
    build_session_gateway_coverage_report,
    load_session_gateway_inventory,
    session_gateway_coverage_lines,
    validate_session_gateway_contract,
    validate_session_gateway_inventory,
    write_session_gateway_coverage_artifact,
)

type CoverageDocumentSource = Literal["snapshot", "live"]


def check_main(argv: Sequence[str] | None = None) -> int:
    args = _parse_coverage_args("Check and export OpenAPI operation marker coverage", argv=argv)
    return _run_coverage_command(
        env_name=str(args.env),
        source=args.source,
        output_path=Path(args.output),
        session_gateway_output_path=Path(args.session_gateway_output),
    )


def _parse_coverage_args(
    description: str,
    *,
    argv: Sequence[str] | None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--env",
        default="local",
        help="environment name to load from environments/<env>.yaml",
    )
    parser.add_argument(
        "--session-gateway-output",
        default=str(repository_root() / "artifacts" / "session-gateway-coverage.json"),
        help="Session Gateway route/case coverage artifact path",
    )
    parser.add_argument(
        "--output",
        default=str(repository_root() / "artifacts" / "openapi-coverage.json"),
        help="coverage artifact path",
    )
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
    output_path: Path,
    session_gateway_output_path: Path,
) -> int:
    try:
        coverage = build_coverage(env_name=env_name, source=source)
        session_gateway_coverage = build_session_gateway_coverage()
        session_gateway_report = build_session_gateway_coverage_report(
            (row for row in coverage if row.contract == "session_gateway"),
            session_gateway_coverage,
        )
        write_coverage_artifact(output_path, coverage)
        write_session_gateway_coverage_artifact(session_gateway_output_path, session_gateway_report)
    except (FileNotFoundError, ValueError, ValidationError, httpx.RequestError) as exc:
        print(f"OpenAPI coverage failed: {exc}", file=sys.stderr)
        return 1

    missing = missing_operation_ids(coverage)
    placeholders = placeholder_operation_ids(coverage)
    print(f"wrote OpenAPI coverage artifact: {output_path}")
    print(f"wrote Session Gateway coverage artifact: {session_gateway_output_path}")
    print(f"operations: {len(coverage)}")
    print(f"missing: {len(missing)}")
    print(f"placeholder: {len(placeholders)}")
    print(f"deferred_admin: {sum(1 for operation in coverage if operation.deferred_admin)}")
    for line in session_gateway_coverage_lines(session_gateway_report):
        print(line)

    if missing:
        print(f"missing OpenAPI markers: {', '.join(missing)}", file=sys.stderr)
        return 1
    if placeholders:
        print(f"placeholder OpenAPI coverage remains: {', '.join(placeholders)}", file=sys.stderr)
        return 1
    uncovered_cases = session_gateway_report.claims.uncovered_executable_case_ids
    if uncovered_cases:
        print(
            "Session Gateway executable cases missing markers: " + ", ".join(uncovered_cases),
            file=sys.stderr,
        )
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

    forbidden_deferrals = load_forbidden_response_deferrals(
        root / "schemas" / "deferred-forbidden-responses.yaml"
    )
    build_operation_request_cases(openapi_doc, required_response_statuses=(401, 403))
    validate_forbidden_response_deferrals(
        openapi_doc,
        forbidden_deferrals,
        deferred_admin_operation_ids=(operation.operation_id for operation in deferred),
    )

    unified_coverage = build_operation_coverage(
        iter_operations(openapi_doc, contract="unified"),
        deferred,
        {key: counts for key, counts in markers.items() if key.contract == "unified"},
        source=source,
    )
    session_gateway_doc = load_snapshot(root / "schemas" / "session-gateway-openapi.yaml")
    validate_session_gateway_contract(session_gateway_doc)
    session_gateway_coverage = build_operation_coverage(
        iter_operations(session_gateway_doc, contract="session_gateway"),
        (),
        {key: counts for key, counts in markers.items() if key.contract == "session_gateway"},
        source="snapshot",
    )
    return unified_coverage + session_gateway_coverage


def build_session_gateway_coverage() -> tuple[SessionGatewayCaseCoverage, ...]:
    root = repository_root()
    openapi_doc = load_snapshot(root / "schemas" / "session-gateway-openapi.yaml")
    validate_session_gateway_contract(openapi_doc)
    inventory = load_session_gateway_inventory(
        root / "schemas" / "session-gateway-inbound-inventory.yaml"
    )
    marker_counts = collect_session_gateway_case_markers_from_tests(root / "tests")
    return validate_session_gateway_inventory(inventory, openapi_doc, marker_counts)


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
    except (FileNotFoundError, ValueError, ValidationError, httpx.RequestError) as exc:
        print(f"OpenAPI refresh failed: {exc}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.write_text(
        json.dumps(openapi_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote OpenAPI snapshot: {output_path}")
    return 0
