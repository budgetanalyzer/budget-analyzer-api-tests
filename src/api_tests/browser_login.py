from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, TypedDict, cast
from urllib.parse import urlencode, urlsplit

import httpx
from playwright.sync_api import BrowserContext, CDPSession, Page, Response, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from api_tests.client import (
    GatewayClient,
    classify_public_rate_policy,
    is_same_origin,
    request_path_from_url,
)
from api_tests.config import EnvironmentConfig
from api_tests.logging import redact_sensitive_text
from api_tests.session import Session


class BrowserLoginError(RuntimeError):
    pass


class BrowserAccountExistsError(BrowserLoginError):
    pass


@dataclass(frozen=True, repr=False)
class BrowserLoginCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


type ExternalHostClassification = Literal["identity-provider", "external-other"]


class PlaywrightCookie(TypedDict, total=False):
    name: str
    value: str
    domain: str
    path: str
    httpOnly: bool
    secure: bool
    sameSite: Literal["Strict", "Lax", "None"]


@dataclass(frozen=True)
class BrowserCookieMetadata:
    name: str
    domain: str
    path: str
    http_only: bool
    secure: bool
    same_site: str


@dataclass(frozen=True)
class BrowserFlowObservation:
    method: str
    status: int
    same_origin_path: str | None = None
    external_host_classification: ExternalHostClassification | None = None


@dataclass(frozen=True, repr=False)
class BrowserLoginResult:
    session: Session = field(repr=False)
    session_cookie: BrowserCookieMetadata
    cookie_names: tuple[str, ...]
    final_path: str
    callback_observed: bool
    observations: tuple[BrowserFlowObservation, ...]


class BrowserFlowObserver:
    def __init__(self, config: EnvironmentConfig) -> None:
        self._config = config
        self._observations: list[BrowserFlowObservation] = []
        self._paced_same_origin_paths: list[str] = []

    def record_paced_same_origin_path(self, path: str) -> None:
        self._paced_same_origin_paths.append(path)

    def record_response(self, response: Response) -> None:
        request_url = response.url
        method = response.request.method
        if is_same_origin(request_url, self._config.origin):
            observation = BrowserFlowObservation(
                method=method,
                status=response.status,
                same_origin_path=request_path_from_url(request_url),
            )
        else:
            observation = BrowserFlowObservation(
                method=method,
                status=response.status,
                external_host_classification=_external_host_classification(
                    request_url,
                    self._config,
                ),
            )
        self._observations.append(observation)

    def result(self) -> tuple[BrowserFlowObservation, ...]:
        return tuple(self._observations)

    def assert_rate_limited_responses_were_paced(self) -> None:
        paced_paths = list(self._paced_same_origin_paths)
        for observation in self._observations:
            path = observation.same_origin_path
            if path is None or classify_public_rate_policy(path) is None:
                continue
            try:
                paced_paths.remove(path)
            except ValueError as exc:
                raise BrowserLoginError(
                    f"same-origin rate-limited browser response bypassed shared pacing: {path}"
                ) from exc


def login_with_browser(
    config: EnvironmentConfig,
    credentials: BrowserLoginCredentials,
) -> Session:
    result = login_flow_with_browser(config, credentials)
    try:
        verify_session(config, result.session)
    except BrowserLoginError:
        _cleanup_failed_browser_session(config, result.session)
        raise
    return result.session


def login_flow_with_browser(
    config: EnvironmentConfig,
    credentials: BrowserLoginCredentials,
    *,
    return_url: str | None = None,
    expected_return_path: str | None = None,
) -> BrowserLoginResult:
    timeout_ms = _browser_timeout_ms(config)
    browser_config = config.auth.browser
    if browser_config is None:
        raise BrowserLoginError("browser login requires browser settings")
    expected_path = expected_return_path or config.browser_auth_contract.default_return_path
    _validate_expected_return_path(expected_path)
    session: Session | None = None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=browser_config.headless)
            try:
                context = browser.new_context()
                try:
                    observer = BrowserFlowObserver(config)
                    page = context.new_page()
                    _pace_public_gateway_requests(context, page, config, observer=observer)
                    page.on("response", observer.record_response)
                    _open_public_login(
                        page,
                        config,
                        timeout_ms=timeout_ms,
                        return_url=return_url,
                    )
                    fill_hosted_login_form(page, credentials, timeout_ms=timeout_ms)
                    _wait_for_application(page, config, timeout_ms=timeout_ms)
                    session = read_session_cookie(context, config.session_cookie_name)
                    session_cookie = read_session_cookie_metadata(
                        context,
                        config.session_cookie_name,
                    )
                    cookie_names = tuple(
                        sorted(
                            cookie["name"]
                            for cookie in cast(list[PlaywrightCookie], context.cookies())
                            if isinstance(cookie.get("name"), str)
                        )
                    )
                    observations = observer.result()
                    observer.assert_rate_limited_responses_were_paced()
                    final_path = _same_origin_page_path(page.url, config)
                    if final_path != expected_path:
                        raise BrowserLoginError(
                            "browser login returned to an unexpected same-origin path: "
                            f"{final_path!r}"
                        )
                    callback_observed = any(
                        observation.same_origin_path == config.browser_auth_contract.callback_path
                        for observation in observations
                    )
                    if not callback_observed:
                        raise BrowserLoginError(
                            "browser login completed without observing the OAuth callback path"
                        )
                finally:
                    context.close()
            finally:
                browser.close()
    except PlaywrightError as exc:
        if session is not None:
            _cleanup_failed_browser_session(config, session)
        raise BrowserLoginError(
            f"browser login failed: {_redact_browser_error(str(exc), credentials)}"
        ) from None
    except BrowserLoginError:
        if session is not None:
            _cleanup_failed_browser_session(config, session)
        raise

    return BrowserLoginResult(
        session=session,
        session_cookie=session_cookie,
        cookie_names=cookie_names,
        final_path=final_path,
        callback_observed=callback_observed,
        observations=observations,
    )


def signup_with_browser(
    config: EnvironmentConfig,
    credentials: BrowserLoginCredentials,
) -> Session:
    timeout_ms = _browser_timeout_ms(config)
    browser_config = config.auth.browser
    if browser_config is None:
        raise BrowserLoginError("browser signup requires browser settings")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=browser_config.headless)
            try:
                context = browser.new_context()
                try:
                    page = context.new_page()
                    _pace_public_gateway_requests(context, page, config)
                    _open_public_login(page, config, timeout_ms=timeout_ms)
                    page.get_by_role("link", name=re.compile("sign up", re.IGNORECASE)).first.click(
                        timeout=timeout_ms
                    )
                    fill_hosted_signup_form(page, credentials, timeout_ms=timeout_ms)
                    try:
                        _wait_for_application(page, config, timeout_ms=timeout_ms)
                    except PlaywrightError as exc:
                        error_text = _hosted_error_text(page)
                        if _is_existing_account_error(error_text):
                            raise BrowserAccountExistsError(
                                "hosted signup reports that the saved local account already exists"
                            ) from exc
                        detail = error_text or str(exc)
                        raise BrowserLoginError(
                            "browser signup did not return to the application: "
                            f"{_redact_browser_error(detail, credentials)}"
                        ) from exc
                    session = read_session_cookie(context, config.session_cookie_name)
                finally:
                    context.close()
            finally:
                browser.close()
    except BrowserLoginError:
        raise
    except PlaywrightError as exc:
        raise BrowserLoginError(
            f"browser signup failed: {_redact_browser_error(str(exc), credentials)}"
        ) from exc

    verify_session(config, session)
    return session


def fill_hosted_login_form(
    page: Page,
    credentials: BrowserLoginCredentials,
    *,
    timeout_ms: int,
) -> None:
    email_field = page.locator(
        'input[name="username"], input[name="email"], input[type="email"]'
    ).first
    password_field = page.locator('input[name="password"], input[type="password"]').first
    submit_button = page.locator(
        'button[type="submit"], button[name="action"], input[type="submit"]'
    ).first

    email_field.fill(credentials.username, timeout=timeout_ms)
    password_field.fill(credentials.password, timeout=timeout_ms)
    submit_button.click(timeout=timeout_ms)


def fill_hosted_signup_form(
    page: Page,
    credentials: BrowserLoginCredentials,
    *,
    timeout_ms: int,
) -> None:
    email_field = page.locator(
        'input[name="email"], input[name="username"], input[type="email"]'
    ).first
    password_field = page.locator('input[name="password"], input[type="password"]').first
    submit_button = page.locator(
        'button[type="submit"], button[name="action"], input[type="submit"]'
    ).first

    email_field.fill(credentials.username, timeout=timeout_ms)
    password_field.fill(credentials.password, timeout=timeout_ms)
    submit_button.click(timeout=timeout_ms)


def read_session_cookie(context: BrowserContext, cookie_name: str) -> Session:
    cookies = context.cookies()
    for cookie in cookies:
        if cookie.get("name") == cookie_name:
            value = cookie.get("value")
            if not isinstance(value, str) or not value:
                raise BrowserLoginError(f"{cookie_name} cookie was present but empty")
            return Session(cookie_name=cookie_name, cookie_value=value)

    raise BrowserLoginError(f"{cookie_name} cookie was not set after hosted login")


def read_session_cookie_metadata(
    context: BrowserContext,
    cookie_name: str,
) -> BrowserCookieMetadata:
    cookies = cast(list[PlaywrightCookie], context.cookies())
    for cookie in cookies:
        if cookie.get("name") == cookie_name:
            return BrowserCookieMetadata(
                name=cookie_name,
                domain=str(cookie.get("domain", "")),
                path=str(cookie.get("path", "")),
                http_only=cookie.get("httpOnly") is True,
                secure=cookie.get("secure") is True,
                same_site=str(cookie.get("sameSite", "")),
            )
    raise BrowserLoginError(f"{cookie_name} cookie metadata was not available after hosted login")


def verify_session(config: EnvironmentConfig, session: Session) -> None:
    try:
        with GatewayClient(config, session=session) as client:
            response = client.raw_request("GET", "/auth/v1/user")
    except httpx.RequestError as exc:
        raise BrowserLoginError(f"session verification request failed: {exc}") from exc

    if response.status_code != httpx.codes.OK:
        raise BrowserLoginError(
            f"GET /auth/v1/user failed after browser login: HTTP {response.status_code}"
        )


def _pace_public_gateway_requests(
    context: BrowserContext,
    page: Page,
    config: EnvironmentConfig,
    *,
    observer: BrowserFlowObserver | None = None,
) -> None:
    session = context.new_cdp_session(page)

    def continue_with_public_gateway_pacing(event: object) -> None:
        _continue_paced_gateway_request(
            session,
            event,
            gateway_origin=config.origin,
            observer=observer,
        )

    session.on("Fetch.requestPaused", continue_with_public_gateway_pacing)
    session.send(
        "Fetch.enable",
        {
            "patterns": [
                {
                    "urlPattern": f"{config.origin}/*",
                    "requestStage": "Request",
                }
            ]
        },
    )


def _continue_paced_gateway_request(
    session: CDPSession,
    event: object,
    *,
    gateway_origin: str,
    observer: BrowserFlowObserver | None = None,
) -> None:
    if not isinstance(event, dict):
        raise BrowserLoginError("browser request interception returned an invalid event")
    request_id = event.get("requestId")
    request = event.get("request")
    if not isinstance(request_id, str) or not isinstance(request, dict):
        raise BrowserLoginError("browser request interception omitted request metadata")
    request_url = request.get("url")
    if not isinstance(request_url, str):
        raise BrowserLoginError("browser request interception omitted its URL")
    if is_same_origin(request_url, gateway_origin):
        path = request_path_from_url(request_url)
        GatewayClient.pace_shared_request(path)
        if observer is not None:
            observer.record_paced_same_origin_path(path)
    session.send("Fetch.continueRequest", {"requestId": request_id})


def _open_public_login(
    page: Page,
    config: EnvironmentConfig,
    *,
    timeout_ms: int,
    return_url: str | None = None,
) -> None:
    query = f"?{urlencode({'returnUrl': return_url})}" if return_url is not None else ""
    response = page.goto(
        f"{config.origin}/oauth2/authorization/idp{query}",
        wait_until="domcontentloaded",
        timeout=timeout_ms,
    )
    if response is not None and response.status >= httpx.codes.BAD_REQUEST:
        raise BrowserLoginError(f"public login entry failed: HTTP {response.status}")


def _wait_for_application(page: Page, config: EnvironmentConfig, *, timeout_ms: int) -> None:
    page.wait_for_url(f"{config.origin}/**", wait_until="networkidle", timeout=timeout_ms)


def _hosted_error_text(page: Page) -> str:
    messages: list[str] = []
    for selector in (
        '[role="alert"]',
        ".ulp-input-error-message",
        "[data-error-code]",
    ):
        locator = page.locator(selector)
        for index in range(locator.count()):
            item = locator.nth(index)
            if item.is_visible():
                text = item.inner_text().strip()
                if text:
                    messages.append(text)
    return " ".join(messages)


def _is_existing_account_error(message: str) -> bool:
    normalized = message.casefold()
    return "already exists" in normalized or "user exists" in normalized


def _redact_browser_error(message: str, credentials: BrowserLoginCredentials) -> str:
    redacted = message.replace(credentials.password, "[REDACTED]")
    redacted = redacted.replace(credentials.username, "[REDACTED]")
    return redact_sensitive_text(redacted)


def _same_origin_page_path(page_url: str, config: EnvironmentConfig) -> str:
    if not is_same_origin(page_url, config.origin):
        raise BrowserLoginError("browser login did not finish on the selected application origin")
    parsed = urlsplit(page_url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _validate_expected_return_path(value: str) -> None:
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "://" in value
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
    ):
        raise BrowserLoginError("expected browser return path must be a safe same-origin path")


def _external_host_classification(
    request_url: str,
    config: EnvironmentConfig,
) -> ExternalHostClassification:
    host = urlsplit(request_url).hostname
    if host is not None and any(
        host.casefold() == suffix or host.casefold().endswith(f".{suffix}")
        for suffix in config.browser_auth_contract.provider_host_suffixes
    ):
        return "identity-provider"
    return "external-other"


def _browser_timeout_ms(config: EnvironmentConfig) -> int:
    return int(config.timeouts.eventually_seconds * 1000)


def _cleanup_failed_browser_session(config: EnvironmentConfig, session: Session) -> None:
    """Best-effort logout of only the session created by the failed browser flow."""
    try:
        with GatewayClient(config, session=session) as client:
            client.raw_request("GET", "/logout")
    except Exception:
        return
