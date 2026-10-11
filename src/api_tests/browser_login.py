from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx
from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from api_tests.config import EnvironmentConfig
from api_tests.session import Session


class BrowserLoginError(RuntimeError):
    pass


class BrowserAccountExistsError(BrowserLoginError):
    pass


@dataclass(frozen=True, repr=False)
class BrowserLoginCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


def login_with_browser(
    config: EnvironmentConfig,
    credentials: BrowserLoginCredentials,
) -> Session:
    timeout_ms = int(config.timeouts.eventually_seconds * 1000)
    browser_config = config.auth.browser
    if browser_config is None:
        raise BrowserLoginError("browser login requires browser settings")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=browser_config.headless)
            try:
                context = browser.new_context()
                try:
                    page = context.new_page()
                    _open_public_login(page, config, timeout_ms=timeout_ms)
                    fill_hosted_login_form(page, credentials, timeout_ms=timeout_ms)
                    _wait_for_application(page, config, timeout_ms=timeout_ms)
                    session = read_session_cookie(context, config.session_cookie_name)
                finally:
                    context.close()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise BrowserLoginError(
            f"browser login failed: {_redact_browser_error(str(exc), credentials)}"
        ) from exc

    verify_session(config, session)
    return session


def signup_with_browser(
    config: EnvironmentConfig,
    credentials: BrowserLoginCredentials,
) -> Session:
    timeout_ms = int(config.timeouts.eventually_seconds * 1000)
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


def verify_session(config: EnvironmentConfig, session: Session) -> None:
    try:
        with httpx.Client(
            base_url=config.origin,
            timeout=config.timeouts.request_seconds,
            cookies={session.cookie_name: session.cookie_value},
        ) as client:
            response = client.get("/auth/v1/user")
    except httpx.RequestError as exc:
        raise BrowserLoginError(f"session verification request failed: {exc}") from exc

    if response.status_code != httpx.codes.OK:
        raise BrowserLoginError(
            f"GET /auth/v1/user failed after browser login: HTTP {response.status_code}"
        )


def _open_public_login(page: Page, config: EnvironmentConfig, *, timeout_ms: int) -> None:
    page.goto(
        f"{config.origin}/oauth2/authorization/idp",
        wait_until="domcontentloaded",
        timeout=timeout_ms,
    )


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
    return redacted.replace(credentials.username, "[REDACTED]")
