# Known Bugs

This directory records deployed or cross-repository defects discovered while exercising the public
Budget Analyzer API surface. These documents are research handoffs, not executable implementation
plans. The owning repository must still define and approve any remediation plan.

Open issues:

- [Shared auth-ingress rate-limit bucket](auth-ingress-shared-rate-limit-bucket.md) — the current
  Istio ingress policy gives all matching auth traffic handled by one ingress proxy instance a
  shared 10-token-per-60-second bucket without a client identity key.
