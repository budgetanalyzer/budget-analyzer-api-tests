from __future__ import annotations

import os
import re
import secrets
import stat
import string
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from api_tests.browser_login import BrowserLoginCredentials
from api_tests.config import EnvironmentConfig, repository_root

LOCAL_ENVIRONMENT_NAME: Final = "local"
LOCAL_ORIGIN: Final = "https://app.budgetanalyzer.localhost"
OWNER_READ_WRITE: Final = stat.S_IRUSR | stat.S_IWUSR
ASSIGNMENT_RE: Final = re.compile(
    r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
)

type SessionRole = Literal["primary", "secondary"]
type CredentialState = Literal["external", "pending", "complete"]
type CredentialGenerator = Callable[[], BrowserLoginCredentials]


class LocalCredentialError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class LocalCredentialRecord:
    credentials: BrowserLoginCredentials
    state: CredentialState


def ensure_exact_local_target(config: EnvironmentConfig) -> None:
    if (
        config.name != LOCAL_ENVIRONMENT_NAME
        or config.environment_type != "local"
        or config.origin != LOCAL_ORIGIN
    ):
        raise LocalCredentialError(
            f"automatic local credentials require --env local resolving exactly to {LOCAL_ORIGIN}"
        )


def load_or_create_local_credentials(
    config: EnvironmentConfig,
    role: SessionRole,
    *,
    environ: Mapping[str, str] | None = None,
    path: Path | None = None,
    generator: CredentialGenerator | None = None,
) -> LocalCredentialRecord:
    # This check deliberately precedes environment and file access.
    ensure_exact_local_target(config)
    username_name, password_name, state_name = _variable_names(config, role)
    source = os.environ if environ is None else environ
    supplied_username = source.get(username_name, "").strip()
    supplied_password = source.get(password_name, "").strip()

    if bool(supplied_username) != bool(supplied_password):
        raise LocalCredentialError(
            f"{role} environment credentials are partial; set both {username_name} and "
            f"{password_name}, or unset both"
        )
    if supplied_username and supplied_password:
        _ensure_distinct_role_username(
            config,
            role,
            supplied_username,
            source=source,
            path=path,
        )
        return LocalCredentialRecord(
            credentials=BrowserLoginCredentials(supplied_username, supplied_password),
            state="external",
        )

    credential_path = path or repository_root() / ".env.local"
    values = _read_credential_file(credential_path)
    stored_username = values.get(username_name, "").strip()
    stored_password = values.get(password_name, "").strip()
    if bool(stored_username) != bool(stored_password):
        raise LocalCredentialError(
            f"{credential_path} contains partial {role} credentials; preserve the existing "
            f"value and set its matching {username_name if not stored_username else password_name}"
        )

    if stored_username and stored_password:
        raw_state = values.get(state_name, "complete").strip()
        if raw_state not in {"pending", "complete"}:
            raise LocalCredentialError(
                f"{credential_path} has invalid {role} account state in {state_name}"
            )
        record = LocalCredentialRecord(
            credentials=BrowserLoginCredentials(stored_username, stored_password),
            state=cast(CredentialState, raw_state),
        )
        _ensure_distinct_role_username(
            config,
            role,
            record.credentials.username,
            source=source,
            path=credential_path,
            values=values,
        )
        return record

    if values.get(state_name, "").strip():
        raise LocalCredentialError(
            f"{credential_path} contains {state_name} without a complete {role} credential pair"
        )

    credentials = (generator or generate_local_credentials)()
    _ensure_distinct_role_username(
        config,
        role,
        credentials.username,
        source=source,
        path=credential_path,
        values=values,
    )
    _update_credential_file(
        credential_path,
        {
            username_name: credentials.username,
            password_name: credentials.password,
            state_name: "pending",
        },
    )
    return LocalCredentialRecord(credentials=credentials, state="pending")


def mark_local_credentials_complete(
    config: EnvironmentConfig,
    role: SessionRole,
    credentials: BrowserLoginCredentials,
    *,
    path: Path | None = None,
) -> None:
    ensure_exact_local_target(config)
    username_name, password_name, state_name = _variable_names(config, role)
    credential_path = path or repository_root() / ".env.local"
    values = _read_credential_file(credential_path)
    if (
        values.get(username_name, "").strip() != credentials.username
        or values.get(password_name, "").strip() != credentials.password
    ):
        raise LocalCredentialError(
            f"{credential_path} changed while provisioning the {role} account; credentials "
            "were preserved and account state was not updated"
        )
    _update_credential_file(credential_path, {state_name: "complete"})


def generate_local_credentials() -> BrowserLoginCredentials:
    username = f"ba-api-test-{secrets.token_hex(12)}@test.com"
    alphabet = string.ascii_letters + string.digits + "!@%_-"
    password = "Ba9!" + "".join(secrets.choice(alphabet) for _ in range(28))
    return BrowserLoginCredentials(username=username, password=password)


def _variable_names(
    config: EnvironmentConfig,
    role: SessionRole,
) -> tuple[str, str, str]:
    browser_config = config.auth.browser
    if browser_config is None:
        raise LocalCredentialError("local browser authentication requires browser settings")
    if role == "primary":
        return (
            browser_config.primary_username_env,
            browser_config.primary_password_env,
            "BA_PRIMARY_TEST_ACCOUNT_STATE",
        )
    return (
        browser_config.secondary_username_env,
        browser_config.secondary_password_env,
        "BA_SECONDARY_TEST_ACCOUNT_STATE",
    )


def _ensure_distinct_role_username(
    config: EnvironmentConfig,
    role: SessionRole,
    username: str,
    *,
    source: Mapping[str, str],
    path: Path | None,
    values: Mapping[str, str] | None = None,
) -> None:
    other_role: SessionRole = "secondary" if role == "primary" else "primary"
    other_username_name, other_password_name, _ = _variable_names(config, other_role)
    external_username = source.get(other_username_name, "").strip()
    external_password = source.get(other_password_name, "").strip()
    if external_username and external_password:
        other_username = external_username
    elif external_username or external_password:
        # The other role will report its own partial-pair error if it is requested.
        return
    else:
        credential_path = path or repository_root() / ".env.local"
        saved_values = values if values is not None else _read_credential_file(credential_path)
        saved_username = saved_values.get(other_username_name, "").strip()
        saved_password = saved_values.get(other_password_name, "").strip()
        if not saved_username or not saved_password:
            return
        other_username = saved_username

    if username == other_username:
        raise LocalCredentialError(
            "primary and secondary local credentials resolve to the same username; "
            "configure two distinct normal users"
        )


def _read_credential_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise LocalCredentialError(f"local credential path is not a file: {path}")
    path.chmod(OWNER_READ_WRITE)
    content = path.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for line_number, line in enumerate(content.splitlines(), start=1):
        match = ASSIGNMENT_RE.match(line)
        if match is None:
            continue
        name = match.group("name")
        if name in values:
            raise LocalCredentialError(
                f"local credential file has duplicate variable {name} on line {line_number}: {path}"
            )
        values[name] = _parse_value(match.group("value"))
    return values


def _parse_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    comment_index = value.find(" #")
    if comment_index >= 0:
        value = value[:comment_index]
    return value.strip()


def _update_credential_file(path: Path, updates: Mapping[str, str]) -> None:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = content.splitlines(keepends=True)
    remaining = dict(updates)
    updated_lines: list[str] = []
    for line in lines:
        match = ASSIGNMENT_RE.match(line.rstrip("\r\n"))
        name = match.group("name") if match is not None else None
        if name is not None and name in remaining:
            updated_lines.append(f"{name}={remaining.pop(name)}\n")
        else:
            updated_lines.append(line)

    if updated_lines and not updated_lines[-1].endswith(("\n", "\r")):
        updated_lines[-1] += "\n"
    if remaining and updated_lines and updated_lines[-1].strip():
        updated_lines.append("\n")
    updated_lines.extend(f"{name}={value}\n" for name, value in remaining.items())

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            os.chmod(temporary_path, OWNER_READ_WRITE)
            temporary_file.writelines(updated_lines)
        os.replace(temporary_path, path)
        temporary_path = None
        path.chmod(OWNER_READ_WRITE)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
