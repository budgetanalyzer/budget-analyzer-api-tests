from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from api_tests.dependency_contract import check_dependency_contract


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate bounded declarations and the frozen uv dependency graph."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: current directory)",
    )
    parser.add_argument(
        "--uv",
        default="uv",
        help="path to the pinned uv executable (default: uv)",
    )
    args = parser.parse_args(argv)
    issues = check_dependency_contract(args.root.resolve(), args.uv)
    if issues:
        for issue in issues:
            print(f"dependency contract error: {issue}", file=sys.stderr)
        return 1
    print("Dependency contract is valid and uv.lock is current.")
    return 0
