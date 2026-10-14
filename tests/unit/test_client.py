from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx
import pytest

import api_tests.client as client_module
from api_tests.client import (
    AUTH_SENSITIVE_RATE_POLICY,
    DiscreteTokenBucketPacer,
    GatewayClient,
    GatewayRequestPacing,
    RequestPacer,
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
    *,
    conservative_initial_wait: bool = True,
) -> GatewayRequestPacing:
    return GatewayRequestPacing(
        gateway_pacer=RequestPacer(
            requests_per_second=8,
            monotonic=timing.monotonic,
            sleep=timing.sleep,
        ),
        auth_sensitive_pacer=DiscreteTokenBucketPacer(
            max_tokens=AUTH_SENSITIVE_RATE_POLICY.max_tokens,
            tokens_per_fill=AUTH_SENSITIVE_RATE_POLICY.tokens_per_fill,
            fill_interval_seconds=AUTH_SENSITIVE_RATE_POLICY.fill_interval_seconds,
            conservative_initial_wait=conservative_initial_wait,
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
    pacing = _request_pacing(timing, conservative_initial_wait=False)
    monkeypatch.setattr(client_module, "_SHARED_GATEWAY_REQUEST_PACING", pacing)

    GatewayClient.pace_shared_request("/assets/app.js")
    GatewayClient.pace_shared_request("/api/v1/transactions")

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
        for request_number in range(11):
            client = primary_client if request_number % 2 == 0 else secondary_client
            client.raw_request("GET", f"/auth/v1/user?request={request_number}")

    assert request_started_at[:10] == pytest.approx(
        [160.0 + (request_number * 0.125) for request_number in range(10)]
    )
    assert request_started_at[10] == pytest.approx(220.0)
    assert timing.sleeps == pytest.approx([60.0, *([0.125] * 9), 58.875])


@pytest.mark.parametrize(
    ("request_target", "expected"),
    [
        ("/login", True),
        ("/login?next=%2F", True),
        ("/login/", False),
        ("/logout", True),
        ("/logout/complete?returnTo=%2F", True),
        ("/auth", True),
        ("/auth/v1/user?probe=1", True),
        ("/oauth2", True),
        ("/oauth2/authorization/idp", True),
        ("/login/oauth2", True),
        ("/login/oauth2/code/idp?code=redacted", True),
        ("/api/v1/transactions", False),
        ("/api-docs", False),
        ("/authentication", False),
    ],
)
def test_auth_sensitive_policy_matches_exact_ingress_path_families(
    request_target: str,
    expected: bool,
) -> None:
    assert AUTH_SENSITIVE_RATE_POLICY.matches(request_target) is expected


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


def test_auth_sensitive_pacer_waits_on_first_use_and_refills_all_tokens_together() -> None:
    timing = FakeTiming()
    pacer = DiscreteTokenBucketPacer(
        max_tokens=10,
        tokens_per_fill=10,
        fill_interval_seconds=60,
        conservative_initial_wait=True,
        monotonic=timing.monotonic,
        sleep=timing.sleep,
    )

    for _ in range(10):
        pacer.wait()

    assert timing.now == pytest.approx(160.0)
    assert timing.sleeps == pytest.approx([60.0])

    pacer.wait()

    assert timing.now == pytest.approx(220.0)
    assert timing.sleeps == pytest.approx([60.0, 60.0])


def test_independent_consecutive_process_budgets_each_use_conservative_first_wait() -> None:
    timing = FakeTiming()

    for _ in range(2):
        pacer = DiscreteTokenBucketPacer(
            max_tokens=10,
            tokens_per_fill=10,
            fill_interval_seconds=60,
            conservative_initial_wait=True,
            monotonic=timing.monotonic,
            sleep=timing.sleep,
        )
        pacer.wait()

    assert timing.now == pytest.approx(220.0)
    assert timing.sleeps == pytest.approx([60.0, 60.0])


def test_auth_sensitive_pacer_serializes_concurrent_callers() -> None:
    timing = FakeTiming()
    pacer = DiscreteTokenBucketPacer(
        max_tokens=10,
        tokens_per_fill=10,
        fill_interval_seconds=60,
        conservative_initial_wait=False,
        monotonic=timing.monotonic,
        sleep=timing.sleep,
    )

    with ThreadPoolExecutor(max_workers=11) as executor:
        completed = list(executor.map(lambda _: pacer.wait(), range(11)))

    assert completed == [None] * 11
    assert timing.now == pytest.approx(160.0)
    assert timing.sleeps == pytest.approx([60.0])


def test_non_auth_api_requests_do_not_consume_auth_sensitive_capacity() -> None:
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

    assert timing.now == pytest.approx(160.125)
    assert timing.sleeps == pytest.approx([0.125, 60.0])


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
        request_pacing=_request_pacing(timing, conservative_initial_wait=False),
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
        request_pacing=_request_pacing(timing, conservative_initial_wait=False),
    ) as gateway_client:
        gateway_client.raw_request("GET", "/auth/v1/user")

    assert len(requests) == 1
    assert "cookie" not in requests[0].headers
    assert "authorization" not in requests[0].headers
