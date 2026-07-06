#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Sequence

from api_tests.tools.openapi_coverage import export_main


def main(argv: Sequence[str] | None = None) -> int:
    return export_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
