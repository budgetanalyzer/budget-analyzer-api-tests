# Security Consensus Hardening Evidence

## Plan 02.1, Phase 2: API-Test OAuth Callback Cookie Preservation

The login helpers already preserve the reusable `__Host-BA_OAUTH_BINDING` cookie
introduced in [Session Gateway phase 1](../../../session-gateway/docs/research/security-consensus-hardening-evidence.md).
No runtime helper changes were required.

`login_flow_with_browser` and `signup_with_browser` keep one Playwright browser
context and page from public login initiation through the hosted form and OAuth
callback. Chromium owns the cookie jar. The request-stage pacing interceptor
continues the original request by id without replacing headers. Session extraction
happens after the callback; only the application session is needed for subsequent
API requests. The direct provider-rejection scenario also reuses one
`GatewayClient`, whose httpx cookie jar carries login-start cookies to its callback.

Regression evidence:

- `tests/unit/auth/test_auth_helpers.py` exercises login and signup with mocked
  Playwright objects, places a synthetic binding cookie at login start, checks it
  remains in the same context at callback submission, and verifies that the real
  pacing interceptor continues the callback without overriding its cookie header.
  Both helpers return the application session and close their browser resources.
- `tests/unit/test_client.py` uses `httpx.MockTransport` with the real
  `GatewayClient` cookie jar. A login-start response sets the binding cookie with
  Secure, HttpOnly, Path=/ and SameSite=Lax; the subsequent denial callback carries
  that cookie. Fake timing keeps pacing deterministic without sleeps.

Validation on 2026-11-07:

- `.venv/bin/python -m pytest tests/unit --env local`: 323 passed, no skips.
- `.venv/bin/python -m ruff format --check .`: passed after formatting the new test.
- `.venv/bin/python -m ruff check .`: passed.
- `.venv/bin/python -m mypy src tests`: passed for 78 source files.
- `git diff --check`: passed.

These are offline unit results. No browser, deployment, identity provider, staging
or production access was required. Mocked Playwright proves helper context reuse;
httpx proves cookie-jar retention but does not enforce browser SameSite semantics.
Actual browser cookie attributes and cross-site delivery remain the responsibility
of [plan 02.1 phase 3](../../../orchestration/docs/plans/security-hardening-02.1-oauth-callback-binding.md).
No new API coverage, snapshot changes, dependency changes, credential changes or
local-state resets were needed in this phase.
