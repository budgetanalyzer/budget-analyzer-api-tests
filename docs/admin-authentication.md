# Admin Authentication Contract

The API harness owns one reusable admin credential slot in addition to the
primary and secondary normal-user slots. It authenticates only through the
public HTTPS browser/gateway lane. Database role assignment remains an external
operator action; the harness never accesses the database, forges authorities,
or substitutes the admin for an ordinary user.

## Identity Sources

Exact-local `browser_preprovisioned` mode lazily generates
`BA_ADMIN_TEST_USERNAME` and `BA_ADMIN_TEST_PASSWORD`, writes them with
`BA_ADMIN_TEST_ACCOUNT_STATE` to owner-only gitignored `.env.local`, and uses
the same pending/complete recovery rules as the ordinary identities. A complete
process-environment pair overrides the saved pair. A partial pair fails without
mixing sources or replacing saved values.

Staging browser mode requires the pre-provisioned admin pair through those same
environment-variable names. `supplied_sessions` mode requires
`BA_ADMIN_SESSION` and never falls back to browser login or signup. A supplied
session must be refreshed externally after grants change. Production configures
no admin session source and collection-skips `admin` and `admin_auth_smoke`
before fixture setup.

Every requested credential slot must resolve to a distinct username. When two
identities participate in one scenario, their `/auth/v1/user` `sub` values must
also differ. Email is not used as the durable database identifier.

## Promotion Stop

Run:

```bash
./run-tests -m admin_auth_smoke
```

An authenticated account with `USER` but not `ADMIN` exits pytest and the
wrapper with code 2. For a generated local account, the message directs the
operator to the username stored in `.env.local`. For external browser
credentials or a supplied session, it names the configured variable source and
prints the redacted-safe public subject. The operator must add `ADMIN` while
retaining `USER`, then rerun the same command.

Browser mode performs a fresh login on rerun. The account is not generated
again. A third successful run is the reuse check.

The browser application sends normal users to the configured default path `/`
and users holding `ADMIN` to the configured admin path `/admin`. Login for the
admin credential slot accepts either destination because the same account is a
normal `USER` before manual promotion and an admin afterward. The public
authorization readiness check below, rather than the redirect alone, decides
whether promotion is complete.

## Authorization Readiness

Admin readiness is established through the public `GET /auth/v1/user` response.
The response must match the checked-in Session Gateway schema and contain:

- roles `USER` and `ADMIN`;
- `transactions:read:any`;
- `users:read` and `users:write`;
- `statementformats:read:any` and `statementformats:write:any`.

Missing `ADMIN` on a valid normal account is the only promotion stop. Missing
seed permissions, missing retained `USER`, malformed responses, non-200 HTTP
responses, TLS/browser failures, and expired supplied sessions fail in their
own categories.

`admin_session` acquires only the admin session. `admin_identity` performs the
readiness request and promotion decision. `admin_gateway_client` depends on
successful `admin_identity` validation. Any fixture that creates business data
for an admin scenario must also depend on `admin_identity`, ensuring promotion
stops before that setup. A scenario that relies on normal-user denial must use
`primary_identity` and `distinct_admin_user_identities`; this verifies the
ordinary role shape and distinct public subjects without eagerly acquiring the
secondary identity.

## Disposable Deactivation Boundary

Run the exact-local lifecycle with:

```bash
./run-tests -m admin_deactivation
```

Collection requires the `admin`, `authorization`, `mutation`, and `destructive`
markers and skips staging, production, supplied sessions, or disabled mutation
policy before network fixtures run. `admin_identity` is resolved before the test
creates its target. The scenario then resolves all three reusable identities and
rejects any target public subject or canonical user id that overlaps them.

One fresh normal target is persisted under a run/scenario-derived variable
prefix in owner-only `.env.local` before hosted signup. Its credentials,
pending/complete state, public subject, and canonical user id stay in that
record. A pending signup attempt within the execution reuses the record instead
of rotating credentials. Completed and interrupted records from earlier runs
remain intact and are never selected as a later target.

The target never receives `ADMIN`. The lifecycle leaves successful targets
deactivated and does not log out their sessions before revocation evidence. It
does not reactivate or delete identity-provider or permission-service accounts,
and it has no janitor. A `503` can represent committed deactivation followed by
failed session revocation, so the harness reads and reports the retained status
without retrying the request.
