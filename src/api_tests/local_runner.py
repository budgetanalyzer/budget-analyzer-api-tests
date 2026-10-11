from __future__ import annotations

import sys
from collections.abc import Sequence

import pytest

from api_tests.config import load_environment
from api_tests.local_credentials import LocalCredentialError, ensure_exact_local_target


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
        ensure_exact_local_target(load_environment("local"))
    except (FileNotFoundError, ValueError, LocalCredentialError) as exc:
        print(f"run-tests local target validation failed: {exc}", file=sys.stderr)
        return 2

    return int(pytest.main(["--env", "local", *arguments]))


if __name__ == "__main__":
    raise SystemExit(main())
