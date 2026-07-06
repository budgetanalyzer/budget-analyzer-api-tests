#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Sequence

from api_tests.tools.validate_environment import main as validate_environment_main


def main(argv: Sequence[str] | None = None) -> int:
    return validate_environment_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
