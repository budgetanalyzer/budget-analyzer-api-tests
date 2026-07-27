from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import yaml
from pydantic import ValidationError

from api_tests.config import load_environment, with_session_mode
from api_tests.prerequisites import (
    PrerequisiteExitCode,
    PrerequisiteScope,
    run_live_preflight,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check prerequisites for a live Budget Analyzer public gateway run",
        epilog=(
            "exit codes: 0 ready, 2 configuration, 3 local-target safety, 4 local trust, "
            "5 DNS, 6 connection, 7 TLS, 8 readiness, 9 gateway, 10 authentication"
        ),
    )
    parser.add_argument(
        "--env",
        default="local",
        help="environment name to load from environments/<env>.yaml",
    )
    parser.add_argument(
        "--scope",
        choices=tuple(scope.value for scope in PrerequisiteScope),
        default=PrerequisiteScope.PUBLIC.value,
        help="highest live prerequisite scope to validate",
    )
    parser.add_argument(
        "--session-mode",
        choices=("supplied_sessions", "browser_preprovisioned"),
        default=None,
        help="override auth.mode from the environment file for this check",
    )
    parser.add_argument(
        "--require-local-target",
        action="store_true",
        help=(
            "require --env local resolving to environment_type local and the exact supported "
            "local HTTPS origin"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    environment_name = str(args.env)
    try:
        config = with_session_mode(load_environment(environment_name), args.session_mode)
    except (FileNotFoundError, ValueError, ValidationError, yaml.YAMLError) as exc:
        print(f"live prerequisite failed [configuration]: {exc}", file=sys.stderr)
        return int(PrerequisiteExitCode.CONFIGURATION)

    result = run_live_preflight(
        config,
        environment_name=environment_name,
        scope=PrerequisiteScope(str(args.scope)),
        require_local_target=bool(args.require_local_target),
    )
    stream = sys.stdout if result.succeeded else sys.stderr
    prefix = "live prerequisites passed" if result.succeeded else "live prerequisite failed"
    print(f"{prefix} [{result.category.value}]: {result.message}", file=stream)
    return int(result.exit_code)
