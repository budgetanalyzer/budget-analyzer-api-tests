from __future__ import annotations

from collections.abc import Callable, Sequence

import httpx
import pytest
from pydantic import ValidationError

from api_tests.config import EnvironmentConfig, load_environment
from api_tests.prerequisites import (
    CommandResult,
    PreflightDependencies,
    PrerequisiteCategory,
    PrerequisiteExitCode,
    PrerequisiteScope,
    run_live_preflight,
)
from api_tests.tools.live_prerequisites import main


def test_exact_local_preflight_invokes_trust_helper_once() -> None:
    command_runs: list[tuple[str, ...]] = []

    def run_command(command: Sequence[str]) -> CommandResult:
        command_runs.append(tuple(command))
        return CommandResult(0, "already trusted", "")

    result = run_live_preflight(
        load_environment("local"),
        environment_name="local",
        scope=PrerequisiteScope.PUBLIC,
        dependencies=_dependencies(run_command=run_command),
    )

    assert result.succeeded
    assert command_runs == [("/test/bin/ensure-budget-analyzer-local-ca-trust",)]


def test_failed_local_trust_bootstrap_stops_before_network() -> None:
    network_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        network_requests.append(request)
        raise AssertionError("network must not open after a trust bootstrap failure")

    result = run_live_preflight(
        load_environment("local"),
        environment_name="local",
        scope=PrerequisiteScope.AUTHENTICATED,
        environ={},
        dependencies=_dependencies(
            run_command=lambda command: CommandResult(
                10,
                "",
                "Host-published Budget Analyzer local CA is missing.",
            ),
            transport=httpx.MockTransport(handler),
        ),
    )

    assert result.category == PrerequisiteCategory.LOCAL_TRUST
    assert result.exit_code == PrerequisiteExitCode.LOCAL_TRUST
    assert "orchestration checkout on the host" in result.message
    assert network_requests == []


@pytest.mark.parametrize(
    ("command_result", "message_fragment"),
    [
        (
            CommandResult(11, "", "Host-published local CA is invalid or expired."),
            "invalid, expired, or stale",
        ),
        (
            CommandResult(13, "", "System CA bundle update failed."),
            "workspace trust helper failed with exit code 13",
        ),
    ],
)
def test_local_trust_bootstrap_distinguishes_publication_and_install_failures(
    command_result: CommandResult,
    message_fragment: str,
) -> None:
    result = run_live_preflight(
        load_environment("local"),
        environment_name="local",
        scope=PrerequisiteScope.PUBLIC,
        dependencies=_dependencies(run_command=lambda command: command_result),
    )

    assert result.category == PrerequisiteCategory.LOCAL_TRUST
    assert message_fragment in result.message


def test_unavailable_trust_helper_falls_through_to_verified_httpx_path() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ready_response(request)

    dependencies = PreflightDependencies(
        find_command=lambda name: None,
        run_command=lambda command: pytest.fail("unavailable command must not be invoked"),
        transport=httpx.MockTransport(handler),
    )
    result = run_live_preflight(
        load_environment("local"),
        environment_name="local",
        scope=PrerequisiteScope.PUBLIC,
        dependencies=dependencies,
    )

    assert result.succeeded
    assert [request.url.path for request in requests] == [
        "/api/v1/currencies",
        "/api-docs/openapi.json",
    ]


@pytest.mark.parametrize(
    ("environment_name", "config"),
    [
        ("staging", load_environment("staging")),
        ("production", load_environment("production")),
        ("local-alias", load_environment("local").model_copy(update={"name": "local-alias"})),
        (
            "local",
            load_environment("local").model_copy(update={"origin": "https://override.example"}),
        ),
    ],
)
def test_non_exact_local_targets_never_invoke_trust_helper(
    environment_name: str,
    config: EnvironmentConfig,
) -> None:
    command_discovery: list[str] = []

    def find_command(name: str) -> str | None:
        command_discovery.append(name)
        return "/test/bin/ensure-budget-analyzer-local-ca-trust"

    result = run_live_preflight(
        config,
        environment_name=environment_name,
        scope=PrerequisiteScope.PUBLIC,
        dependencies=PreflightDependencies(
            find_command=find_command,
            run_command=lambda command: pytest.fail("trust helper must remain local-only"),
            transport=httpx.MockTransport(_ready_response),
        ),
    )

    assert result.succeeded
    assert command_discovery == []


@pytest.mark.parametrize(
    ("environment_name", "config"),
    [
        ("staging", load_environment("staging")),
        ("production", load_environment("production")),
        ("local-alias", load_environment("local").model_copy(update={"name": "local-alias"})),
        (
            "local",
            load_environment("local").model_copy(update={"origin": "https://override.example"}),
        ),
    ],
)
def test_require_local_target_refuses_mismatch_before_command_or_network(
    environment_name: str,
    config: EnvironmentConfig,
) -> None:
    result = run_live_preflight(
        config,
        environment_name=environment_name,
        scope=PrerequisiteScope.AUTHENTICATED,
        require_local_target=True,
        environ={},
        dependencies=PreflightDependencies(
            find_command=lambda name: pytest.fail("command discovery must not run"),
            run_command=lambda command: pytest.fail("command execution must not run"),
            transport=httpx.MockTransport(lambda request: pytest.fail("network must not open")),
        ),
    )

    assert result.category == PrerequisiteCategory.TARGET_SAFETY
    assert result.exit_code == PrerequisiteExitCode.TARGET_SAFETY


def test_environment_config_rejects_http_origin() -> None:
    raw_config = load_environment("local").model_dump()
    raw_config["origin"] = "http://app.budgetanalyzer.localhost"

    with pytest.raises(ValidationError, match="absolute HTTPS URL"):
        EnvironmentConfig.model_validate(raw_config)


@pytest.mark.parametrize(
    ("raised", "category", "message_fragment"),
    [
        (
            httpx.ConnectError("Name or service not known"),
            PrerequisiteCategory.DNS,
            "DNS resolution failed",
        ),
        (
            httpx.ConnectError("Connection refused"),
            PrerequisiteCategory.CONNECTION,
            "was refused",
        ),
        (
            httpx.ConnectTimeout("timed out"),
            PrerequisiteCategory.CONNECTION,
            "timed out",
        ),
        (
            httpx.ConnectError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: hostname mismatch"
            ),
            PrerequisiteCategory.TLS,
            "hostname validation failed",
        ),
        (
            httpx.ConnectError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate expired"
            ),
            PrerequisiteCategory.TLS,
            "expiry or validity",
        ),
        (
            httpx.ConnectError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"
            ),
            PrerequisiteCategory.TLS,
            "issuer validation failed",
        ),
        (
            httpx.ConnectError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed "
                "certificate in certificate chain"
            ),
            PrerequisiteCategory.TLS,
            "trust-chain validation failed",
        ),
    ],
)
def test_network_failures_are_classified(
    raised: httpx.RequestError,
    category: PrerequisiteCategory,
    message_fragment: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised

    result = run_live_preflight(
        load_environment("local"),
        environment_name="local",
        scope=PrerequisiteScope.PUBLIC,
        dependencies=_dependencies(transport=httpx.MockTransport(handler)),
    )

    assert result.category == category
    assert message_fragment in result.message


def test_gateway_failure_is_distinct_from_backend_readiness() -> None:
    def gateway_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, json={})

    gateway_result = run_live_preflight(
        load_environment("local"),
        environment_name="local",
        scope=PrerequisiteScope.PUBLIC,
        dependencies=_dependencies(transport=httpx.MockTransport(gateway_handler)),
    )

    def readiness_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/currencies":
            return httpx.Response(401, headers={"content-type": "application/json"}, json={})
        return httpx.Response(503, headers={"content-type": "application/json"}, json={})

    readiness_result = run_live_preflight(
        load_environment("local"),
        environment_name="local",
        scope=PrerequisiteScope.PUBLIC,
        dependencies=_dependencies(transport=httpx.MockTransport(readiness_handler)),
    )

    assert gateway_result.category == PrerequisiteCategory.GATEWAY
    assert readiness_result.category == PrerequisiteCategory.READINESS


def test_browser_authentication_scope_reports_all_missing_variables() -> None:
    result = run_live_preflight(
        load_environment("local"),
        environment_name="local",
        scope=PrerequisiteScope.AUTHENTICATED,
        environ={},
        dependencies=_dependencies(),
    )

    assert result.category == PrerequisiteCategory.AUTHENTICATION
    assert "BA_PRIMARY_TEST_USERNAME" in result.message
    assert "BA_PRIMARY_TEST_PASSWORD" in result.message
    assert "BA_SECONDARY_TEST_USERNAME" in result.message
    assert "BA_SECONDARY_TEST_PASSWORD" in result.message


def test_supplied_authorization_scope_requires_primary_and_secondary_sessions() -> None:
    config = load_environment("local")
    config = config.model_copy(
        update={"auth": config.auth.model_copy(update={"mode": "supplied_sessions"})}
    )

    result = run_live_preflight(
        config,
        environment_name="local",
        scope=PrerequisiteScope.AUTHORIZATION,
        environ={},
        dependencies=_dependencies(),
    )

    assert result.category == PrerequisiteCategory.AUTHENTICATION
    assert "BA_PRIMARY_SESSION" in result.message
    assert "BA_SECONDARY_SESSION" in result.message


def test_cli_returns_stable_configuration_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--env", "does-not-exist"])

    captured = capsys.readouterr()
    assert exit_code == PrerequisiteExitCode.CONFIGURATION
    assert captured.out == ""
    assert "live prerequisite failed [configuration]" in captured.err


def _dependencies(
    *,
    run_command: Callable[[Sequence[str]], CommandResult] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> PreflightDependencies:
    return PreflightDependencies(
        find_command=lambda name: "/test/bin/ensure-budget-analyzer-local-ca-trust",
        run_command=run_command or (lambda command: CommandResult(0, "already trusted", "")),
        transport=transport or httpx.MockTransport(_ready_response),
    )


def _ready_response(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/currencies":
        return httpx.Response(401, headers={"content-type": "application/json"}, json={})
    if request.url.path == "/api-docs/openapi.json":
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"openapi": "3.1.0", "paths": {}},
        )
    raise AssertionError(f"unexpected preflight request: {request.url}")
