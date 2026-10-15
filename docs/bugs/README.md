# Known Bugs

This directory records deployed or cross-repository defects discovered while exercising the public
Budget Analyzer API surface. These documents are research handoffs, not executable implementation
plans. The owning repository must still define and approve any remediation plan.

Open issues: none.

Resolved issues:

- [Shared auth-ingress rate-limit bucket](auth-ingress-shared-rate-limit-bucket.md) — resolved on
  2026-10-15 by four independent per-client ingress policies, followed by consecutive public
  browser/session and API regression runs.
