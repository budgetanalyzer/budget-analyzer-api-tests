from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from api_tests.config import EnvironmentConfig
    from api_tests.run_state import RunState


@dataclass(frozen=True, repr=False)
class SessionCookie:
    name: str
    value: str = field(repr=False)
    domain: str
    path: str


@dataclass(frozen=True)
class SessionIdentity:
    label: str
    session_cookie: SessionCookie = field(repr=False)


@dataclass(frozen=True)
class SessionBundle:
    primary: SessionIdentity
    secondary: SessionIdentity | None = None


@dataclass(frozen=True)
class SessionContext:
    mode: str
    bundle: SessionBundle


class MissingSecondarySessionError(RuntimeError):
    pass


def secondary_only_bundle(bundle: SessionBundle) -> SessionBundle:
    if bundle.secondary is None:
        raise MissingSecondarySessionError(
            "secondary session prerequisite failed: selected environment did not provide a "
            "secondary session"
        )
    return SessionBundle(primary=bundle.secondary)


class SessionAcquirer(Protocol):
    def __call__(
        self,
        config: EnvironmentConfig,
        run_state: RunState,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> SessionBundle: ...
