from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Session:
    cookie_name: str
    cookie_value: str = field(repr=False)


class MissingSecondarySessionError(RuntimeError):
    pass
