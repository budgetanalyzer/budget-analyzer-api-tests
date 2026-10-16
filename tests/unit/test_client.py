from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest

import api_tests.client as client_module
from api_tests.client import (
    API_RATE_POLICY,
    LOGIN_COMPLETION_RATE_POLICY,
    LOGIN_INITIATION_RATE_POLICY,
    SESSION_OPERATIONS_RATE_POLICY,
    GatewayClient,
    GatewayRequestPacing,
    PublicRatePolicyName,
    RequestPacer,
    classify_public_rate_policy,
    is_same_origin,
)
from api_tests.config import load_environment


@dataclass
class FakeTiming:
    now: float = 100.0
    sleep_overshoot: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds + self.sleep_overshoot


def _request_pacing(
    timing: FakeTiming,
) -> GatewayRequestPacing:
    return GatewayRequestPacing(
        login_initiation_pacer=RequestPacer(
            requests_per_second=LOGIN_INITIATION_RATE_POLICY.requests_per_second,
            monotonic=timing.monotonic,
            sleep=timing.sleep,
        ),
        login_completion_pacer=RequestPacer(
            requests_per_second=LOGIN_COMPLETION_RATE_POLICY.requests_per_second,
            monotonic=timing.monotonic,
            sleep=timing.sleep,
        ),
        session_operations_pacer=RequestPacer(
            requests_per_second=SESSION_OPERATIONS_RATE_POLICY.requests_per_second,
            monotonic=timing.monotonic,
            sleep=timing.sleep,
        ),
        api_requests_pacer=RequestPacer(
            requests_per_second=API_RATE_POLICY.requests_per_second,
            monotonic=timing.monotonic,
            sleep=timing.sleep,
        ),
    )


def test_request_pacer_uses_monotonic_fixed_intervals() -> None:
    timing = FakeTiming()
    pacer = RequestPacer(
        requests_per_second=8,
        monotonic=timing.monotonic,
        sleep=timing.sleep,
    )

    pacer.wait()
    pacer.wait()
    pacer.wait()

    assert timing.now == pytest.approx(100.25)
    assert timing.sleeps == pytest.approx([0.125, 0.125])


def test_request_pacer_preserves_interval_after_sleep_overshoots() -> None:
    timing = FakeTiming(now=0.0, sleep_overshoot=0.2)
    pacer = RequestPacer(
        requests_per_second=8,
        monotonic=timing.monotonic,
        sleep=timing.sleep,
    )
    released_at: list[float] = []

    for _ in range(3):
        pacer.wait()
        released_at.append(timing.monotonic())

    assert released_at == pytest.approx([0.0, 0.325, 0.650])
    assert timing.sleeps == pytest.approx([0.125, 0.125])


def test_non_httpx_gateway_requests_use_shared_pacer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timing = FakeTiming()
    pacing = _request_pacing(timing)
    monkeypatch.setattr(client_module, "_SHARED_GATEWAY_REQUEST_PACING", pacing)

    GatewayClient.pace_shared_request("/api/v1/transactions")
    GatewayClient.pace_shared_request("/api/v1/currencies?base=USD")

    assert timing.now == pytest.approx(100.125)
    assert timing.sleeps == pytest.approx([0.125])


def test_gateway_clients_share_one_default_pacer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_environment("local")
    timing = FakeTiming()
    pacing = _request_pacing(timing)
    monkeypatch.setattr(client_module, "_SHARED_GATEWAY_REQUEST_PACING", pacing)
    request_started_at: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_started_at.append(timing.monotonic())
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    with (
        GatewayClient(config, transport=transport) as primary_client,
        GatewayClient(config, transport=transport) as secondary_client,
    ):
        primary_client.raw_request("GET", "/auth/v1/user?client=primary")
        GatewayClient.pace_shared_request("/auth/v1/session?source=browser")
        secondary_client.raw_request("GET", "/logout?client=secondary")

    assert request_started_at == pytest.approx([100.0, 100.5])
    assert timing.sleeps == pytest.approx([0.25, 0.25])


@pytest.mark.parametrize(
    ("request_target", "expected"),
    [
        ("/oauth2", PublicRatePolicyName.LOGIN_INITIATION),
        ("/oauth2/authorization/idp?returnUrl=%2F", PublicRatePolicyName.LOGIN_INITIATION),
        ("/login/oauth2", PublicRatePolicyName.LOGIN_COMPLETION),
        ("/login/oauth2/code/idp?code=redacted", PublicRatePolicyName.LOGIN_COMPLETION),
        ("/auth", PublicRatePolicyName.SESSION_OPERATIONS),
        ("/auth/v1/user?probe=1", PublicRatePolicyName.SESSION_OPERATIONS),
        ("/logout", PublicRatePolicyName.SESSION_OPERATIONS),
        ("/logout/complete?returnTo=%2F", PublicRatePolicyName.SESSION_OPERATIONS),
        ("/api", PublicRatePolicyName.API_REQUESTS),
        ("/api/v1/transactions?probe=1", PublicRatePolicyName.API_REQUESTS),
        ("/login", None),
        ("/login?next=%2F", None),
        ("/login/", None),
        ("/api-docs", None),
        ("/oauth2ish", None),
        ("/login/oauth2ish", None),
        ("/authentication", None),
        ("/logoutish", None),
        ("/apiv1", None),
    ],
)
def test_request_targets_match_exact_ingress_policy_families(
    request_target: str,
    expected: PublicRatePolicyName | None,
) -> None:
    policy = classify_public_rate_policy(request_target)

    assert (policy.name if policy is not None else None) is expected


@pytest.mark.parametrize(
    ("request_url", "expected"),
    [
        ("https://app.budgetanalyzer.localhost/auth/v1/user", True),
        ("https://APP.BUDGETANALYZER.LOCALHOST:443/oauth2/start", True),
        ("http://app.budgetanalyzer.localhost/auth/v1/user", False),
        ("https://identity.example.test/auth/v1/user", False),
        ("/auth/v1/user", False),
    ],
)
def test_same_origin_classification_excludes_external_identity_provider_urls(
    request_url: str,
    expected: bool,
) -> None:
    assert is_same_origin(request_url, "https://app.budgetanalyzer.localhost") is expected


def test_public_policy_pacers_are_independent_and_have_no_first_use_wait() -> None:
    timing = FakeTiming()
    pacing = _request_pacing(timing)

    for target in (
        "/api/v1/transactions",
        "/api/v1/currencies",
        "/auth/v1/user",
        "/auth/v1/session",
        "/login/oauth2/code/idp",
        "/login/oauth2/code/idp?state=redacted",
        "/oauth2/authorization/idp",
        "/oauth2/authorization/idp?returnUrl=%2F",
    ):
        pacing.wait(target)

    assert timing.now == pytest.approx(102.25)
    assert timing.sleeps == pytest.approx([0.125, 0.25, 0.625, 1.25])


def test_api_requests_do_not_consume_session_operations_pace() -> None:
    config = load_environment("local")
    timing = FakeTiming()
    pacing = _request_pacing(timing)

    with GatewayClient(
        config,
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        request_pacing=pacing,
    ) as gateway_client:
        gateway_client.api_request("GET", "/v1/transactions")
        gateway_client.api_request("GET", "/v1/currencies")
        gateway_client.raw_request("GET", "/auth/v1/user")

    assert timing.now == pytest.approx(100.125)
    assert timing.sleeps == pytest.approx([0.125])


def test_gateway_client_returns_429_without_retrying() -> None:
    config = load_environment("local")
    timing = FakeTiming()
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        nonlocal request_count
        request_count += 1
        return httpx.Response(httpx.codes.TOO_MANY_REQUESTS)

    with GatewayClient(
        config,
        transport=httpx.MockTransport(handler),
        request_pacing=_request_pacing(timing),
    ) as gateway_client:
        response = gateway_client.api_request("GET", "/v1/transactions")

    assert response.status_code == httpx.codes.TOO_MANY_REQUESTS
    assert request_count == 1
    assert timing.sleeps == []


def test_gateway_client_without_session_sends_no_authentication_headers() -> None:
    config = load_environment("local")
    timing = FakeTiming()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(httpx.codes.UNAUTHORIZED)

    with GatewayClient(
        config,
        transport=httpx.MockTransport(handler),
        request_pacing=_request_pacing(timing),
    ) as gateway_client:
        gateway_client.raw_request("GET", "/auth/v1/user")

    assert len(requests) == 1
    assert "cookie" not in requests[0].headers
    assert "authorization" not in requests[0].headers
