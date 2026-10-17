from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Session:
    cookie_name: str
    cookie_value: str = field(repr=False)


class MissingSecondarySessionError(RuntimeError):
    pass


class MissingAdminSessionError(RuntimeError):
    pass


class IndistinctSessionsError(RuntimeError):
    pass


def ensure_distinct_sessions(primary: Session, secondary: Session) -> None:
    if (
        primary.cookie_name == secondary.cookie_name
        and primary.cookie_value == secondary.cookie_value
    ):
        raise IndistinctSessionsError(
            "primary and secondary sessions resolve to the same authenticated session"
        )
