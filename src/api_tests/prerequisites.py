from __future__ import annotations

import errno
import os
import shutil
import socket
import ssl
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum

import httpx

from api_tests.client import GatewayClient
from api_tests.config import EnvironmentConfig

LOCAL_ENVIRONMENT_NAME = "local"
LOCAL_ORIGIN = "https://app.budgetanalyzer.localhost"
LOCAL_TRUST_COMMAND = "ensure-budget-analyzer-local-ca-trust"


class PrerequisiteScope(StrEnum):
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    AUTHORIZATION = "authorization"


class PrerequisiteCategory(StrEnum):
    READY = "ready"
    CONFIGURATION = "configuration"
    TARGET_SAFETY = "target_safety"
    LOCAL_TRUST = "local_trust"
    DNS = "dns"
    CONNECTION = "connection"
    TLS = "tls"
    READINESS = "readiness"
    GATEWAY = "gateway"
    AUTHENTICATION = "authentication"


class PrerequisiteExitCode(IntEnum):
    OK = 0
    CONFIGURATION = 2
    TARGET_SAFETY = 3
    LOCAL_TRUST = 4
    DNS = 5
    CONNECTION = 6
    TLS = 7
    READINESS = 8
    GATEWAY = 9
    AUTHENTICATION = 10


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandFinder = Callable[[str], str | None]
CommandRunner = Callable[[Sequence[str]], CommandResult]


@dataclass(frozen=True, slots=True)
class PreflightDependencies:
    find_command: CommandFinder
    run_command: CommandRunner
    transport: httpx.BaseTransport | None = None


@dataclass(frozen=True, slots=True)
class PrerequisiteResult:
    category: PrerequisiteCategory
    exit_code: PrerequisiteExitCode
    message: str

    @property
    def succeeded(self) -> bool:
        return self.exit_code == PrerequisiteExitCode.OK


def default_preflight_dependencies() -> PreflightDependencies:
    return PreflightDependencies(
        find_command=shutil.which,
        run_command=_run_command,
    )


def run_live_preflight(
    config: EnvironmentConfig,
    *,
    environment_name: str,
    scope: PrerequisiteScope,
    require_local_target: bool = False,
    environ: Mapping[str, str] | None = None,
    dependencies: PreflightDependencies | None = None,
) -> PrerequisiteResult:
    if require_local_target and not is_exact_local_target(config, environment_name):
        return _failure(
            PrerequisiteCategory.TARGET_SAFETY,
            PrerequisiteExitCode.TARGET_SAFETY,
            "--require-local-target requires --env local resolving to environment_type "
            f"'local' and origin {LOCAL_ORIGIN}",
        )

    selected_dependencies = dependencies or default_preflight_dependencies()
    bootstrap_result = _bootstrap_exact_local_trust(
        config,
        environment_name=environment_name,
        dependencies=selected_dependencies,
    )
    if bootstrap_result is not None:
        return bootstrap_result

    public_result = _check_public_gateway(config, transport=selected_dependencies.transport)
    if public_result is not None:
        return public_result

    if scope != PrerequisiteScope.PUBLIC:
        credential_result = _check_authentication_inputs(
            config,
            scope=scope,
            environ=os.environ if environ is None else environ,
        )
        if credential_result is not None:
            return credential_result

    return PrerequisiteResult(
        category=PrerequisiteCategory.READY,
        exit_code=PrerequisiteExitCode.OK,
        message=f"{scope.value} prerequisites passed for {config.origin}",
    )


def is_exact_local_target(config: EnvironmentConfig, environment_name: str) -> bool:
    return (
        environment_name == LOCAL_ENVIRONMENT_NAME
        and config.name == LOCAL_ENVIRONMENT_NAME
        and config.environment_type == "local"
        and config.origin == LOCAL_ORIGIN
    )


def _run_command(command: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _bootstrap_exact_local_trust(
    config: EnvironmentConfig,
    *,
    environment_name: str,
    dependencies: PreflightDependencies,
) -> PrerequisiteResult | None:
    if not is_exact_local_target(config, environment_name):
        return None

    command_path = dependencies.find_command(LOCAL_TRUST_COMMAND)
    if command_path is None:
        return None

    try:
        result = dependencies.run_command((command_path,))
    except OSError as exc:
        return _local_trust_failure(f"could not execute the workspace trust helper: {exc}")

    if result.returncode == 0:
        return None

    output = f"{result.stdout}\n{result.stderr}".lower()
    if "missing" in output or result.returncode == 10:
        detail = "the host-published local CA is missing"
    elif "invalid" in output or "expired" in output or result.returncode == 11:
        detail = "the host-published local CA is invalid, expired, or stale"
    else:
        detail = f"the workspace trust helper failed with exit code {result.returncode}"
    return _local_trust_failure(detail)


def _local_trust_failure(detail: str) -> PrerequisiteResult:
    return _failure(
        PrerequisiteCategory.LOCAL_TRUST,
        PrerequisiteExitCode.LOCAL_TRUST,
        f"{detail}; run ./setup.sh from the orchestration checkout on the host, then retry",
    )


def _check_public_gateway(
    config: EnvironmentConfig,
    *,
    transport: httpx.BaseTransport | None,
) -> PrerequisiteResult | None:
    try:
        with GatewayClient(config, transport=transport) as client:
            edge_response = client.api_request("GET", "/v1/currencies")
            if edge_response.status_code != httpx.codes.UNAUTHORIZED:
                return _failure(
                    PrerequisiteCategory.GATEWAY,
                    PrerequisiteExitCode.GATEWAY,
                    "unexpected unauthenticated gateway behavior for GET /api/v1/currencies: "
                    f"expected HTTP 401, received HTTP {edge_response.status_code}",
                )
            if _is_html_response(edge_response):
                return _failure(
                    PrerequisiteCategory.GATEWAY,
                    PrerequisiteExitCode.GATEWAY,
                    "unexpected unauthenticated gateway behavior for GET /api/v1/currencies: "
                    "received an HTML response",
                )

            docs_response = client.raw_request("GET", config.openapi_path)
            if docs_response.status_code != httpx.codes.OK:
                return _failure(
                    PrerequisiteCategory.READINESS,
                    PrerequisiteExitCode.READINESS,
                    f"ingress or backend readiness failed for GET {config.openapi_path}: "
                    f"HTTP {docs_response.status_code}",
                )
            if not _is_openapi_document(docs_response):
                return _failure(
                    PrerequisiteCategory.READINESS,
                    PrerequisiteExitCode.READINESS,
                    f"GET {config.openapi_path} did not return a public OpenAPI JSON document",
                )
    except httpx.TimeoutException:
        return _failure(
            PrerequisiteCategory.CONNECTION,
            PrerequisiteExitCode.CONNECTION,
            f"connection to {config.origin} timed out; confirm Tilt or the target environment "
            "is up",
        )
    except httpx.ConnectError as exc:
        return _classify_connect_error(config.origin, exc)
    except (ssl.SSLError, OSError) as exc:
        if _contains_tls_failure(exc):
            return _tls_failure(config.origin, exc)
        return _failure(
            PrerequisiteCategory.CONNECTION,
            PrerequisiteExitCode.CONNECTION,
            f"could not connect to {config.origin}; confirm Tilt or the target environment is up",
        )
    except httpx.RequestError:
        return _failure(
            PrerequisiteCategory.CONNECTION,
            PrerequisiteExitCode.CONNECTION,
            f"could not connect to {config.origin}; confirm Tilt or the target environment is up",
        )

    return None


def _is_html_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    return "text/html" in content_type or response.text.lstrip().lower().startswith("<html")


def _is_openapi_document(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return (
        isinstance(body, dict)
        and isinstance(body.get("openapi"), str)
        and isinstance(body.get("paths"), dict)
    )


def _classify_connect_error(origin: str, exc: httpx.ConnectError) -> PrerequisiteResult:
    if _contains_exception_type(exc, socket.gaierror) or _message_contains(
        exc, "name or service not known", "nodename nor servname", "temporary failure in name"
    ):
        return _failure(
            PrerequisiteCategory.DNS,
            PrerequisiteExitCode.DNS,
            f"DNS resolution failed for {origin}",
        )
    if _contains_tls_failure(exc):
        return _tls_failure(origin, exc)
    if _contains_connection_refused(exc):
        return _failure(
            PrerequisiteCategory.CONNECTION,
            PrerequisiteExitCode.CONNECTION,
            f"connection to {origin} was refused; confirm Tilt or the target environment is up",
        )
    return _failure(
        PrerequisiteCategory.CONNECTION,
        PrerequisiteExitCode.CONNECTION,
        f"could not connect to {origin}; confirm Tilt or the target environment is up",
    )


def _tls_failure(origin: str, exc: BaseException) -> PrerequisiteResult:
    message = _exception_text(exc)
    if "hostname" in message or "not valid for" in message:
        detail = "certificate hostname validation failed"
    elif "expired" in message or "not yet valid" in message:
        detail = "certificate expiry or validity validation failed"
    elif "issuer" in message or "unable to get local" in message:
        detail = "certificate issuer validation failed"
    elif "self-signed" in message or "certificate_verify_failed" in message:
        detail = "certificate trust-chain validation failed"
    else:
        detail = "TLS certificate validation failed"
    return _failure(
        PrerequisiteCategory.TLS,
        PrerequisiteExitCode.TLS,
        f"{detail} for {origin}; install the required CA in the runner trust store",
    )


def _check_authentication_inputs(
    config: EnvironmentConfig,
    *,
    scope: PrerequisiteScope,
    environ: Mapping[str, str],
) -> PrerequisiteResult | None:
    required_names: list[str] = []
    if config.auth.mode == "browser_preprovisioned":
        browser = config.auth.browser
        if browser is None:
            return _authentication_failure("browser credential variable names are not configured")
        required_names.extend(
            (
                browser.primary_username_env,
                browser.primary_password_env,
                browser.secondary_username_env,
                browser.secondary_password_env,
            )
        )
    else:
        supplied = config.auth.supplied_sessions
        required_names.append(supplied.primary_session_env)
        if scope == PrerequisiteScope.AUTHORIZATION:
            if supplied.secondary_session_env is None:
                return _authentication_failure(
                    "a secondary supplied session path is not configured for authorization scope"
                )
            required_names.append(supplied.secondary_session_env)

    missing_names = sorted(name for name in required_names if not environ.get(name, "").strip())
    if missing_names:
        return _authentication_failure(
            "missing configured credential/session environment variables: "
            + ", ".join(missing_names)
        )
    return None


def _authentication_failure(message: str) -> PrerequisiteResult:
    return _failure(
        PrerequisiteCategory.AUTHENTICATION,
        PrerequisiteExitCode.AUTHENTICATION,
        message,
    )


def _contains_connection_refused(exc: BaseException) -> bool:
    for current in _exception_chain(exc):
        if isinstance(current, OSError) and current.errno == errno.ECONNREFUSED:
            return True
    return _message_contains(exc, "connection refused", "all connection attempts failed")


def _contains_tls_failure(exc: BaseException) -> bool:
    return _contains_exception_type(exc, ssl.SSLError) or _message_contains(
        exc,
        "certificate_verify_failed",
        "certificate verify failed",
        "hostname mismatch",
        "self-signed certificate",
    )


def _contains_exception_type(exc: BaseException, exception_type: type[BaseException]) -> bool:
    return any(isinstance(current, exception_type) for current in _exception_chain(exc))


def _message_contains(exc: BaseException, *needles: str) -> bool:
    text = _exception_text(exc)
    return any(needle in text for needle in needles)


def _exception_text(exc: BaseException) -> str:
    return " ".join(str(current).lower() for current in _exception_chain(exc))


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return tuple(chain)


def _failure(
    category: PrerequisiteCategory,
    exit_code: PrerequisiteExitCode,
    message: str,
) -> PrerequisiteResult:
    return PrerequisiteResult(category=category, exit_code=exit_code, message=message)
