from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

RUN_ID_PATTERN = re.compile(r"^ba-api-test-\d{8}T\d{6}Z-[a-z0-9]{8}$")


@dataclass(frozen=True)
class RunState:
    run_id: str


def generate_run_id(
    *,
    now: datetime | None = None,
    random_suffix: str | None = None,
) -> str:
    timestamp = (now or datetime.now(tz=UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = random_suffix or secrets.token_hex(4)
    run_id = f"ba-api-test-{timestamp}-{suffix}"
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(f"generated run id does not match required format: {run_id}")
    return run_id


def new_run_state() -> RunState:
    return RunState(run_id=generate_run_id())


@cache
def session_run_state() -> RunState:
    return new_run_state()
