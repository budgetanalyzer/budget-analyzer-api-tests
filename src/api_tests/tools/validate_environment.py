from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import yaml
from pydantic import ValidationError

from api_tests.config import load_environment, normalized_non_secret_settings


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a Budget Analyzer API test environment")
    parser.add_argument(
        "--env",
        default="local",
        help="environment name to load from environments/<env>.yaml",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_environment(str(args.env))
    except (FileNotFoundError, ValueError, ValidationError, yaml.YAMLError) as exc:
        print(f"environment validation failed: {exc}", file=sys.stderr)
        return 1

    print(yaml.safe_dump(normalized_non_secret_settings(config), sort_keys=False).strip())
    return 0
