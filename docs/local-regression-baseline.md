# Local Regression Baseline

Accepted on 2026-10-11 from the agent container against
`https://app.budgetanalyzer.localhost` using the exact local
`browser_preprovisioned` workflow.

## Test Revision And Contract

- Repository HEAD: `a5859f8f23f3c8ff128121c9a06573c4dcd28ed8` on branch
  `initial-import-2`, plus the uncommitted Phase 6 worktree changes represented
  by this document and the accompanying implementation. Record the eventual
  commit that contains this baseline when the worktree is committed.
- OpenAPI snapshot: `schemas/openapi.json`, SHA-256
  `13d5a1a699a9733d07f05880945196b55ccc28682a42f744583c41856bf499c7`.
- Runtime: CPython 3.12.3 with pytest 9.1.1, httpx 0.28.1, Playwright 1.63.0,
  Pydantic 2.14.0, Ruff 0.17.0, and mypy 2.4.0.
- The exact installed package inventory is retained in each ignored acceptance
  run's `dependencies.txt`; both files have SHA-256
  `b6afe9944312deacb99b78129124b4070f0722842d2391d038eab698a01cbf68`.
- No public response or repository configuration exposed service image,
  deployment, or source revision identifiers. None are claimed, and no
  Kubernetes or service-internal source was consulted to manufacture them.

## Accepted Runs

Both runs were complete no-argument `./run-tests` executions. The second began
immediately after the first. The same owner-only `.env.local` was retained and
was byte-for-byte unchanged across both runs; no credentials, hashes of secret
material, or session cookies are recorded here. Existing user data was not
reset or cleaned between runs.

| Run artifact | Result | Duration | Coverage and drift |
| --- | --- | ---: | --- |
| `artifacts/runs/20261011T185048Z-3b607a6a/` | 119 passed; 0 failed, errored, skipped, or xfailed | 56.94 s | 40 operations; 8 deferred admin; live contract matched |
| `artifacts/runs/20261011T185150Z-4321a530/` | 119 passed; 0 failed, errored, skipped, or xfailed | 56.93 s | 40 operations; 8 deferred admin; live contract matched |

The two runs produced identical coverage and drift artifacts. The drift report
contains zero differences from the checked-in snapshot. Request execution
complied with the fixed public NGINX policy: one per-client-IP bucket at 10
requests/second with `burst=50` and `nodelay`, with the harness conservatively
pacing public-gateway request starts at no more than 8 requests/second. This
includes `GatewayClient` requests and same-origin browser-authentication
traffic. HTTP 429 responses are not retried and remain failures.

## Deferred Scope And Limits

The eight deferred admin/global operations remain:

- `searchTransactions` and `countTransactionsAcrossUsers`
- currency `create` and `update`
- `importLatestExchangeRates`
- `getUsers`, `getUser`, and `deactivateUser`

They require a separate admin identity, cleanup, and global-state safety plan.
This baseline does not cover staging, production mutation, CI/CD, browser UI
behavior beyond public authentication, performance, or service internals.
Production defaults remain read-only.

The accepted local runs leave namespaced user-owned records behind by design.
The run id and resources created by each test define the assertion boundary;
there is no generic cleanup or assumption of an empty database.

## Spring Upgrade Use

Run `./run-tests` with no arguments against the upgraded local deployment using
the retained identities and data. Investigate any test, coverage, HTTP 429, or
live-drift failure before changing expectations. Refresh `schemas/openapi.json`
only through the separate intentional review command when a public contract
change is accepted; never update the snapshot merely to make an upgrade pass.
