# Requirement Draft — Partner API v2 Authentication

## Summary
Partners must authenticate using short-lived JWTs. Cookie-based partner sessions
are deprecated.

## Functional requirements
1. The system shall issue JWTs with a 15-minute expiry for partner clients.
2. The system shall accept refresh tokens valid for 7 days.
3. Partners shall migrate by **March 28, 2026** (launch window closes end of day).
4. The API shall return `401` with error code `token_expired` when a JWT is stale.

## Non-functional requirements
- Auth changes must pass security review prior to production enablement.
- PII (partner contact emails) must not appear in auth failure logs.
- A rollback plan is required for the public API contract change.

## Acceptance criteria
- [ ] Staging soak ≥ 48 hours with error rate < 0.5%
- [ ] Security review signed off
- [ ] Rollback runbook linked from the change ticket

## Assumptions
- Partners can rotate credentials without a dedicated self-service UI in v1 of this draft.
- Clock skew under 30 seconds is acceptable; larger skew is out of scope for this release.

## Open questions
- Should refresh-token rotation be mandatory on every use?
- Is March 28 a hard cutover or a soft deprecation start?
