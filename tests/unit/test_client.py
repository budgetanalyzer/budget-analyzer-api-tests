from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest

import api_tests.client as client_module
from api_tests.client import GatewayClient, RequestPacer
from api_tests.config import load_environment


@dataclass
class FakeTiming:
    now: float = 100.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


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


def test_non_httpx_gateway_requests_use_shared_pacer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timing = FakeTiming()
    pacer = RequestPacer(
        requests_per_second=8,
        monotonic=timing.monotonic,
        sleep=timing.sleep,
    )
    monkeypatch.setattr(client_module, "_SHARED_GATEWAY_REQUEST_PACER", pacer)

    GatewayClient.pace_shared_request()
    GatewayClient.pace_shared_request()

    assert timing.now == pytest.approx(100.125)
    assert timing.sleeps == pytest.approx([0.125])


def test_gateway_clients_share_one_default_pacer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_environment("local")
    timing = FakeTiming()
    pacer = RequestPacer(
        requests_per_second=8,
        monotonic=timing.monotonic,
        sleep=timing.sleep,
    )
    monkeypatch.setattr(client_module, "_SHARED_GATEWAY_REQUEST_PACER", pacer)
    request_started_at: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_started_at.append(timing.monotonic())
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    with (
        GatewayClient(config, transport=transport) as primary_client,
        GatewayClient(config, transport=transport) as secondary_client,
    ):
        primary_client.api_request("GET", "/v1/transactions")
        secondary_client.raw_request("GET", "/api-docs/openapi.json")
        primary_client.api_request("GET", "/v1/currencies")

    assert request_started_at == pytest.approx([100.0, 100.125, 100.25])
    assert timing.sleeps == pytest.approx([0.125, 0.125])


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
        request_pacer=RequestPacer(
            requests_per_second=8,
            monotonic=timing.monotonic,
            sleep=timing.sleep,
        ),
    ) as gateway_client:
        response = gateway_client.api_request("GET", "/v1/transactions")

    assert response.status_code == httpx.codes.TOO_MANY_REQUESTS
    assert request_count == 1
    assert timing.sleeps == []
