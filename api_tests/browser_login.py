from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from playwright.sync_api import BrowserContext, Page, sync_playwright

from api_tests.config import EnvironmentConfig


class BrowserLoginError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class BrowserLoginCredentials:
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True, repr=False)
class SessionCookie:
    name: str
    value: str = field(repr=False)
    domain: str
    path: str


def login_with_browser(
    config: EnvironmentConfig,
    credentials: BrowserLoginCredentials,
) -> SessionCookie:
    timeout_ms = int(config.timeouts.eventually_seconds * 1000)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=config.auth.browser.headless)
        try:
            context = browser.new_context(ignore_https_errors=not config.verify_tls)
            try:
                page = context.new_page()
                page.goto(
                    f"{config.origin}/oauth2/authorization/idp",
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                fill_auth0_login_form(page, credentials, timeout_ms=timeout_ms)
                page.wait_for_url(f"{config.origin}/**", wait_until="networkidle", timeout=timeout_ms)
                cookie = read_session_cookie(context, config.session_cookie_name)
            finally:
                context.close()
        finally:
            browser.close()

    verify_session_cookie(config, cookie)
    return cookie


def fill_auth0_login_form(
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

    email_field.fill(credentials.email, timeout=timeout_ms)
    password_field.fill(credentials.password, timeout=timeout_ms)
    submit_button.click(timeout=timeout_ms)


def read_session_cookie(context: BrowserContext, cookie_name: str) -> SessionCookie:
    cookies = context.cookies()
    for cookie in cookies:
        if cookie.get("name") == cookie_name:
            value = cookie.get("value")
            domain = cookie.get("domain")
            path = cookie.get("path")
            if not isinstance(value, str) or not value:
                raise BrowserLoginError(f"{cookie_name} cookie was present but empty")
            if not isinstance(domain, str) or not isinstance(path, str):
                raise BrowserLoginError(f"{cookie_name} cookie was missing domain or path metadata")
            return SessionCookie(name=cookie_name, value=value, domain=domain, path=path)

    raise BrowserLoginError(f"{cookie_name} cookie was not set after Auth0 login")


def verify_session_cookie(config: EnvironmentConfig, cookie: SessionCookie) -> None:
    with httpx.Client(
        base_url=config.origin,
        verify=config.verify_tls,
        timeout=config.timeouts.request_seconds,
        cookies={cookie.name: cookie.value},
    ) as client:
        response = client.get("/auth/v1/user")

    if response.status_code != httpx.codes.OK:
        raise BrowserLoginError(
            f"GET /auth/v1/user failed after browser login: HTTP {response.status_code}"
        )


def cookie_value(cookie: SessionCookie) -> str:
    return cookie.value


def cookie_metadata(cookie: SessionCookie) -> dict[str, Any]:
    return {"name": cookie.name, "domain": cookie.domain, "path": cookie.path}
