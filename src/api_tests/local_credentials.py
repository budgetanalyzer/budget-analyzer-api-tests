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

type SessionRole = Literal["primary", "secondary", "admin"]
type CredentialState = Literal["external", "pending", "complete"]
type CredentialGenerator = Callable[[], BrowserLoginCredentials]

DISPOSABLE_RECORD_PREFIX: Final = "BA_ADMIN_DEACTIVATION_"


class LocalCredentialError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class LocalCredentialRecord:
    credentials: BrowserLoginCredentials
    state: CredentialState


@dataclass(frozen=True, repr=False)
class DisposableCredentialRecord:
    run_id: str
    scenario_id: str
    credentials: BrowserLoginCredentials
    state: Literal["pending", "complete"]
    subject: str | None = None
    user_id: str | None = None


def load_or_create_disposable_credentials(
    config: EnvironmentConfig,
    run_id: str,
    scenario_id: str,
    *,
    path: Path | None = None,
    generator: CredentialGenerator | None = None,
) -> DisposableCredentialRecord:
    """Load only this execution's target, or persist a fresh target before signup."""
    ensure_exact_local_target(config)
    prefix = _disposable_variable_prefix(run_id, scenario_id)
    credential_path = path or repository_root() / ".env.local"
    values = _read_credential_file(credential_path)
    names = _disposable_variable_names(prefix)
    stored_run_id = values.get(names.run_id, "").strip()
    stored_scenario_id = values.get(names.scenario_id, "").strip()
    stored_username = values.get(names.username, "").strip()
    stored_password = values.get(names.password, "").strip()
    stored_state = values.get(names.state, "").strip()
    stored_subject = values.get(names.subject, "").strip()
    stored_user_id = values.get(names.user_id, "").strip()
    current_values = (
        stored_run_id,
        stored_scenario_id,
        stored_username,
        stored_password,
        stored_state,
        stored_subject,
        stored_user_id,
    )

    if any(current_values):
        if not all((stored_run_id, stored_scenario_id, stored_username, stored_password)):
            raise LocalCredentialError(
                f"{credential_path} contains an incomplete disposable deactivation record; "
                "preserve it for diagnosis and restore its matching fields"
            )
        if stored_run_id != run_id or stored_scenario_id != scenario_id:
            raise LocalCredentialError(
                f"{credential_path} disposable deactivation provenance does not match the "
                "current run and scenario"
            )
        if stored_state not in {"pending", "complete"}:
            raise LocalCredentialError(
                f"{credential_path} has invalid disposable account state in {names.state}"
            )
        if bool(stored_subject) != bool(stored_user_id):
            raise LocalCredentialError(
                f"{credential_path} contains a partial disposable identity binding"
            )
        if stored_subject and stored_state != "complete":
            raise LocalCredentialError(
                f"{credential_path} binds an identity to an incomplete disposable account"
            )
        credentials = BrowserLoginCredentials(stored_username, stored_password)
        _ensure_disposable_username_is_distinct(
            config,
            credentials.username,
            prefix=prefix,
            values=values,
        )
        return DisposableCredentialRecord(
            run_id=run_id,
            scenario_id=scenario_id,
            credentials=credentials,
            state=cast(Literal["pending", "complete"], stored_state),
            subject=stored_subject or None,
            user_id=stored_user_id or None,
        )

    credentials = (generator or generate_local_credentials)()
    _ensure_disposable_username_is_distinct(
        config,
        credentials.username,
        prefix=prefix,
        values=values,
    )
    _update_credential_file(
        credential_path,
        {
            names.run_id: run_id,
            names.scenario_id: scenario_id,
            names.username: credentials.username,
            names.password: credentials.password,
            names.state: "pending",
        },
    )
    return DisposableCredentialRecord(run_id, scenario_id, credentials, "pending")


def mark_disposable_credentials_complete(
    config: EnvironmentConfig,
    record: DisposableCredentialRecord,
    *,
    path: Path | None = None,
) -> DisposableCredentialRecord:
    ensure_exact_local_target(config)
    credential_path = path or repository_root() / ".env.local"
    prefix = _disposable_variable_prefix(record.run_id, record.scenario_id)
    names = _disposable_variable_names(prefix)
    values = _read_credential_file(credential_path)
    _require_saved_disposable_record(values, names, record, credential_path)
    _update_credential_file(credential_path, {names.state: "complete"})
    return DisposableCredentialRecord(
        record.run_id,
        record.scenario_id,
        record.credentials,
        "complete",
        record.subject,
        record.user_id,
    )


def bind_disposable_identity(
    config: EnvironmentConfig,
    record: DisposableCredentialRecord,
    *,
    current_run_id: str,
    current_scenario_id: str,
    subject: str,
    user_id: str,
    reusable_subjects: frozenset[str],
    reusable_user_ids: frozenset[str],
    path: Path | None = None,
) -> DisposableCredentialRecord:
    """Bind public and canonical identity only after exact execution ownership checks."""
    ensure_exact_local_target(config)
    _require_current_disposable_provenance(record, current_run_id, current_scenario_id)
    if record.state != "complete":
        raise LocalCredentialError("disposable target must complete signup before identity binding")
    if not subject or not user_id:
        raise LocalCredentialError("disposable identity binding requires subject and user id")
    if subject in reusable_subjects or user_id in reusable_user_ids:
        raise LocalCredentialError("refusing to bind a reusable identity as a disposable target")

    credential_path = path or repository_root() / ".env.local"
    prefix = _disposable_variable_prefix(record.run_id, record.scenario_id)
    names = _disposable_variable_names(prefix)
    values = _read_credential_file(credential_path)
    _require_saved_disposable_record(values, names, record, credential_path)
    for name, value in values.items():
        if (
            name.startswith(DISPOSABLE_RECORD_PREFIX)
            and name.endswith("_IDP_SUBJECT")
            and name != names.subject
            and value.strip() == subject
        ):
            raise LocalCredentialError(
                "disposable public subject is already bound to another execution record"
            )
        if (
            name.startswith(DISPOSABLE_RECORD_PREFIX)
            and name.endswith("_USER_ID")
            and name != names.user_id
            and value.strip() == user_id
        ):
            raise LocalCredentialError(
                "disposable canonical user id is already bound to another execution record"
            )
    if record.subject is not None and record.subject != subject:
        raise LocalCredentialError("disposable public subject changed after identity binding")
    if record.user_id is not None and record.user_id != user_id:
        raise LocalCredentialError("disposable canonical user id changed after identity binding")
    _update_credential_file(
        credential_path,
        {names.subject: subject, names.user_id: user_id},
    )
    return DisposableCredentialRecord(
        record.run_id,
        record.scenario_id,
        record.credentials,
        "complete",
        subject,
        user_id,
    )


def require_owned_disposable_identity(
    record: DisposableCredentialRecord,
    *,
    current_run_id: str,
    current_scenario_id: str,
    subject: str,
    user_id: str,
    reusable_subjects: frozenset[str],
    reusable_user_ids: frozenset[str],
) -> None:
    _require_current_disposable_provenance(record, current_run_id, current_scenario_id)
    if record.state != "complete" or record.subject is None or record.user_id is None:
        raise LocalCredentialError("disposable target lacks a completed identity binding")
    if record.subject != subject or record.user_id != user_id:
        raise LocalCredentialError("disposable target identity does not match saved provenance")
    if subject in reusable_subjects or user_id in reusable_user_ids:
        raise LocalCredentialError("refusing to treat a reusable identity as disposable")


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
    if role == "secondary":
        return (
            browser_config.secondary_username_env,
            browser_config.secondary_password_env,
            "BA_SECONDARY_TEST_ACCOUNT_STATE",
        )
    return (
        browser_config.admin_username_env,
        browser_config.admin_password_env,
        "BA_ADMIN_TEST_ACCOUNT_STATE",
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
    credential_path = path or repository_root() / ".env.local"
    saved_values = values if values is not None else _read_credential_file(credential_path)
    for other_role in ("primary", "secondary", "admin"):
        if other_role == role:
            continue
        other_username_name, other_password_name, _ = _variable_names(config, other_role)
        external_username = source.get(other_username_name, "").strip()
        external_password = source.get(other_password_name, "").strip()
        if external_username and external_password:
            other_username = external_username
        elif external_username or external_password:
            # The other role reports its own partial-pair error when requested.
            continue
        else:
            saved_username = saved_values.get(other_username_name, "").strip()
            saved_password = saved_values.get(other_password_name, "").strip()
            if not saved_username or not saved_password:
                continue
            other_username = saved_username

        if username == other_username:
            raise LocalCredentialError(
                f"{role} and {other_role} local credentials resolve to the same username; "
                "configure distinct identities for every credential slot"
            )


@dataclass(frozen=True)
class _DisposableVariableNames:
    run_id: str
    scenario_id: str
    username: str
    password: str
    state: str
    subject: str
    user_id: str


def _disposable_variable_prefix(run_id: str, scenario_id: str) -> str:
    if not run_id or not scenario_id:
        raise LocalCredentialError("disposable provenance requires non-empty run and scenario ids")
    token = re.sub(r"[^A-Za-z0-9]+", "_", f"{run_id}_{scenario_id}").strip("_").upper()
    if not token:
        raise LocalCredentialError("disposable provenance did not produce a safe record name")
    return f"{DISPOSABLE_RECORD_PREFIX}{token}"


def _disposable_variable_names(prefix: str) -> _DisposableVariableNames:
    return _DisposableVariableNames(
        run_id=f"{prefix}_RUN_ID",
        scenario_id=f"{prefix}_SCENARIO_ID",
        username=f"{prefix}_USERNAME",
        password=f"{prefix}_PASSWORD",
        state=f"{prefix}_ACCOUNT_STATE",
        subject=f"{prefix}_IDP_SUBJECT",
        user_id=f"{prefix}_USER_ID",
    )


def _ensure_disposable_username_is_distinct(
    config: EnvironmentConfig,
    username: str,
    *,
    prefix: str,
    values: Mapping[str, str],
) -> None:
    for role in ("primary", "secondary", "admin"):
        username_name, password_name, _ = _variable_names(config, role)
        external_username = os.environ.get(username_name, "").strip()
        external_password = os.environ.get(password_name, "").strip()
        if external_username and external_password:
            reusable_username = external_username
        elif external_username or external_password:
            continue
        else:
            saved_username = values.get(username_name, "").strip()
            saved_password = values.get(password_name, "").strip()
            if not saved_username or not saved_password:
                continue
            reusable_username = saved_username
        if username == reusable_username:
            raise LocalCredentialError("disposable username resolves to a reusable credential slot")
    current_username_name = _disposable_variable_names(prefix).username
    for name, value in values.items():
        if (
            name.startswith(DISPOSABLE_RECORD_PREFIX)
            and name.endswith("_USERNAME")
            and name != current_username_name
            and value.strip() == username
        ):
            raise LocalCredentialError(
                "disposable username is already retained by another execution record"
            )


def _require_saved_disposable_record(
    values: Mapping[str, str],
    names: _DisposableVariableNames,
    record: DisposableCredentialRecord,
    path: Path,
) -> None:
    expected = {
        names.run_id: record.run_id,
        names.scenario_id: record.scenario_id,
        names.username: record.credentials.username,
        names.password: record.credentials.password,
        names.state: record.state,
    }
    if any(values.get(name, "").strip() != value for name, value in expected.items()):
        raise LocalCredentialError(
            f"{path} changed while provisioning the disposable account; retained credentials "
            "and provenance were not replaced"
        )


def _require_current_disposable_provenance(
    record: DisposableCredentialRecord,
    current_run_id: str,
    current_scenario_id: str,
) -> None:
    if record.run_id != current_run_id or record.scenario_id != current_scenario_id:
        raise LocalCredentialError(
            "disposable target provenance does not belong to the current run and scenario"
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
