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

## Plan 02.7, Phase 3: POST Heartbeat And Logout API-Test Callers

Validated on 2026-11-07 for worker run
`20261107T161240Z-phase-3-135f9029-5c1d-4d55-9c89-1dffd07e58c3`.

The black-box callers now use `POST /auth/v1/session` for heartbeat and
`POST /logout` for logout, including failed-browser cleanup, stale-cookie replay,
OAuth lifecycle cleanup, and admin-deactivation verification. Logout assertions and
the checked-in Session Gateway contract require `303 See Other`. The inbound-route
inventory retains the same 14 routes and 35 scenarios while recording the two
mutating routes as POST. Heartbeat tests are mutations and no longer part of the
production-safe read selection.

Regression evidence:

- Focused helper, contract, assertion, pacing, and deactivation tests: 103 passed.
- `.venv/bin/python -m pytest tests/unit --env local`: 324 passed, no skips.

This was an offline phase. It did not contact local, staging, or production
deployments, mutate cluster or application state, add Origin enforcement, or add new
API coverage. Exact-Origin enforcement remains assigned to plan 02.8, and synchronized
live verification remains assigned to the later orchestration phase. No local-state
reset is required for this HTTP method change.

## Plan 02.8, Phase 1: Exact-Origin API-Test Client

`GatewayClient.raw_request`, and therefore `api_request`, adds the environment's
configured `origin` as the `Origin` header to every method other than `GET`,
`HEAD`, and `OPTIONS`. Method matching is case-insensitive. Other caller-supplied
headers are preserved, and safe methods send no `Origin`. The header is CSRF
provenance for the planned exact-Origin ingress `DENY`. It is not authentication
for arbitrary HTTP clients. Browser-driven requests rely on Chromium's own
`Origin` behavior and are unchanged.

Regression evidence:

- New unit tests cover `POST`, `PUT`, `PATCH`, `DELETE`, lowercase `post`, the
  API and `/logout` lanes, and the absence of `Origin` on `GET`, `HEAD`, and
  `OPTIONS`.
- `.venv/bin/python -m pytest tests/unit --env local`: 332 passed.
- `.venv/bin/python -m pytest tests/api --env local --collect-only`: 105 collected.
- `ruff format --check`, `ruff check`, and `mypy src tests`: clean.

This was an offline phase. It did not contact any deployment or add API coverage.
The header is harmless without the ingress rule, and the orchestration phase
adds that rule. No local-state reset is required.
