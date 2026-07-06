#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

import yaml

from api_tests.config import load_environment, normalized_non_secret_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a Budget Analyzer API test environment")
    parser.add_argument(
        "--env",
        default="local",
        help="environment name to load from environments/<env>.yaml",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_environment(args.env)
    except Exception as exc:
        print(f"environment validation failed: {exc}", file=sys.stderr)
        return 1

    print(yaml.safe_dump(normalized_non_secret_settings(config), sort_keys=False).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
