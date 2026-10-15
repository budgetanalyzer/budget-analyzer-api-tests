# Shared Auth-Ingress Rate-Limit Bucket

- **Status:** Open
- **Severity:** High availability risk
- **Discovered:** 2026-10-14
- **Owning area:** Orchestration / Istio ingress
- **Observed by:** Budget Analyzer public black-box API tests

## Summary

The public Istio ingress rate-limits all matching authentication traffic with one local descriptor
bucket that has no client IP, session, or user identity component. Every matching request handled
by the same ingress Envoy instance competes for 10 tokens, replenished as a batch every 60 seconds.

This is not a rolling per-client limit. With one ingress replica, it is effectively one shared
system-wide auth bucket. With multiple replicas, each replica has an independent shared bucket, but
clients assigned to the same replica still affect one another and aggregate enforcement varies with
load balancing.

The issue is an availability and fairness defect. It is not an authentication bypass or direct
data-confidentiality defect.

## Current Request Paths

The matcher covers the public application authority and these path families:

- `/login`;
- `/logout` and descendants;
- `/auth` and descendants, including `/auth/v1/user` and `/auth/v1/session`;
- `/oauth2` and descendants;
- `/login/oauth2` and descendants, including the identity-provider callback.

The match is path-based and does not distinguish HTTP method, authentication state, response
status, or caller identity. Normal browser lifecycle traffic, invalid unauthenticated probes, and
deliberate abuse consume the same capacity.

## Configuration Evidence

The source policy is
`../orchestration/kubernetes/istio/ingress-rate-limit.yaml`.

Its virtual-host action emits only this descriptor entry for matching authority and path values:

```text
auth_path=auth-sensitive
```

The corresponding local descriptor bucket is:

```yaml
token_bucket:
  max_tokens: 10
  tokens_per_fill: 10
  fill_interval: 60s
```

There is no `remote_address`, trusted client-address header, session identifier, user identifier,
or other varying descriptor entry. The local rate-limit response adds:

```text
x-local-rate-limit: auth-sensitive
```

The production overlay at
`../orchestration/kubernetes/production/istio-ingress-policies/kustomization.yaml` changes only the
matched authority to the production hostname. It does not change the descriptor identity or token
budget.

This policy is separate from the NGINX `/api` policy. NGINX currently limits API requests per
derived client IP at 10 requests/second with `burst=50`; the ingress auth policy does not have the
equivalent per-client identity dimension.

## Black-Box Evidence

The Session Gateway coverage run initially found a legitimate schema mismatch in unauthorized
responses. After that service defect was corrected, missing-session and invalid-session responses
matched the reviewed OpenAPI schema, but the focused public suite still failed with HTTP `429`.

The rejected responses were emitted by `istio-envoy`, contained a plain-text
`local_rate_limited` body, and carried:

```text
x-local-rate-limit: auth-sensitive
```

The same exhaustion had two observable forms:

- direct `/auth/v1/user` requests returned HTTP `429` instead of their expected `200` or `401`;
- browser session acquisition either received HTTP `429` at the public authorization entry or
  timed out waiting for the OAuth callback to return to the application.

A primary-only authentication smoke passed after the bucket refilled. A subsequent focused run
again exhausted the bucket after login and several Session Gateway requests. This isolates the
remaining failure from credentials and from the corrected Session Gateway response serialization.

No credentials, cookies, authorization codes, or OAuth state values are needed to reproduce or
diagnose the issue.

## Impact

### Trivial denial of service

An unauthenticated caller can consume the shared descriptor tokens with requests to a matching
path. Legitimate callers routed through the same ingress instance then receive HTTP `429` until the
next refill. Repeating that traffic once per refill interval can keep the affected auth lane
unavailable at very low request volume.

### Normal traffic competes with abuse traffic

The shared bucket combines several unlike operations:

- beginning an OAuth login;
- completing an OAuth callback;
- reading current-user information;
- refreshing and inspecting a session through heartbeat;
- logging out;
- malformed, invalid-session, and unknown auth-route requests.

One caller can therefore prevent an unrelated caller from completing a flow already in progress.
In particular, rejecting the callback after a successful hosted login creates a confusing browser
timeout rather than a clear login-entry rejection.

### Capacity below plausible normal use

The browser heartbeat is routine application traffic. If clients heartbeat approximately every
three minutes, ten requests per minute supports only about thirty evenly distributed active clients
per ingress instance before accounting for user reads, login, callback, logout, bursts, retries by
real browsers, or malicious traffic. Real traffic is bursty, so the practical limit is lower.

### Replica-dependent behavior

Envoy's local rate limiter is not a shared cluster-wide service. Adding ingress replicas creates
more independent buckets rather than adding per-client fairness. Outcomes can change with replica
count and load-balancer assignment, and a determined caller can still consume capacity on each
instance it reaches.

## Relationship To Test-Harness Pacing

The black-box harness deliberately paces its own traffic below public deployment limits. All
`GatewayClient` instances in one process and same-origin browser requests share pacing because they
normally leave through the same client IP.

The Session Gateway coverage plan currently allows the harness to model the deployed 10-token
auth bucket so existing coverage can finish without generating avoidable HTTP `429` responses.
That is a temporary compatibility measure; it does not fix this defect:

- pacing one test process does not prevent another client from consuming the shared ingress
  bucket;
- separate test processes do not share in-memory pacing state;
- successful paced tests do not prove per-client isolation;
- retaining the defective budget in tests after remediation would unnecessarily slow the suite and
  encode the wrong public contract.

When the ingress policy is corrected, update the harness's auth-sensitive path classes and limits
to match the reviewed per-client policies. Primary, secondary, unauthenticated, and browser traffic
from one process should continue to share each applicable per-client pacer.

## Recommended Remediation

### 1. Add a trustworthy client identity

At minimum, include a client-specific descriptor dimension for auth-edge limits. Derive it from an
ingress-controlled source, not an arbitrary caller-supplied `X-Forwarded-For` value. Prove the
identity model with requests from two distinct downstream clients and with forged forwarding
headers, as is already done for the NGINX API limiter.

If production uses multiple ingress replicas and requires one consistent budget per client, use a
distributed/global rate-limit service or another shared enforcement mechanism. Per-replica local
buckets can still be acceptable when their approximate aggregate semantics are explicit.

### 2. Separate traffic classes

Do not retain one budget for every auth-related path. Define independently reviewable policies for:

- login and OAuth authorization entry, with a strict per-client abuse limit;
- OAuth callback, with enough independent capacity to complete legitimate in-progress flows;
- `/auth/v1/user` and `/auth/v1/session`, with a substantially higher normal-application budget;
- logout, with its own modest per-client limit.

Choose exact values from expected browser behavior, concurrency, callback bursts, heartbeat
frequency, ingress replica count, and an explicit abuse model. Do not infer production-safe values
from the API test suite's request count.

### 3. Clarify edge-versus-gateway ownership

Consider making Istio ingress the owner of broad public per-client abuse controls for both `/auth`
and `/api`, while NGINX retains only resource-specific protection for unusually expensive backend
operations. Alternatively, keep the blanket API limit at NGINX, but document why identity and
policy ownership differ by route family.

Avoid two blanket `/api` buckets with similar limits unless their distinct purposes, ordering, and
client-visible HTTP `429` responses are intentional. If an ingress API limiter should protect
`ext_authz` itself, verify and enforce its filter ordering rather than assuming that insertion
before Envoy's router places it before external authorization.

### 4. Preserve diagnosability

Keep a non-secret response marker that identifies which policy rejected a request. Prefer distinct
markers and metrics for login entry, callback, session-machine APIs, logout, and any API edge
policy. Dashboards and alerts should expose accepted and rejected counts per policy without logging
cookies, OAuth state, authorization codes, or credentials.

## Remediation Validation

A future fix should include static and live proof that:

1. rendered local and production manifests use the intended client identity and separate route
   classes;
2. one client can exhaust its login budget without causing a second client to receive HTTP `429`;
3. forged forwarding headers cannot select a fresh bucket;
4. OAuth callback capacity remains available for a legitimate flow after unrelated login-entry
   traffic;
5. sustained current-user and heartbeat traffic at the supported concurrency does not consume the
   login budget;
6. logout remains available under expected normal traffic;
7. behavior is understood and tested with multiple ingress replicas;
8. rate-limit response markers and metrics identify the rejecting policy;
9. the public black-box Session Gateway suite passes twice consecutively without deployment
   exceptions or HTTP `429`; and
10. the test harness is updated to pace against the corrected per-client contracts rather than the
    temporary shared 10-token policy.

## Relevant Sources

- `../orchestration/kubernetes/istio/ingress-rate-limit.yaml`
- `../orchestration/kubernetes/production/istio-ingress-policies/kustomization.yaml`
- `../orchestration/docs/architecture/session-edge-authorization-pattern.md`
- `../orchestration/docs/architecture/security-architecture.md`
- `../orchestration/scripts/smoketest/verify-istio-ingress.sh`
- `../orchestration/scripts/smoketest/verify-edge-browser-hardening.sh`
- `../orchestration/scripts/smoketest/verify-api-rate-limit-identity.sh`
- `src/api_tests/client.py`
- `src/api_tests/browser_login.py`
- `docs/plans/session-gateway-black-box-coverage.md`
