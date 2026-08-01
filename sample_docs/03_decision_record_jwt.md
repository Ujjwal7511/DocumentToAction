# Decision Record — ADR-014: JWT Issuance for Partner API

## Status
Accepted — 2026-03-11

## Context
Partner integrations currently rely on long-lived cookie sessions. This complicates
horizontal scaling and prevents fine-grained revocation. Engineering proposes
moving partners to JWT bearer tokens.

## Decision
We will issue RS256-signed JWTs for partner API authentication and keep legacy
cookie sessions available only until **March 18, 2026**.

Note: This deadline conflicts with earlier verbal targets that mentioned March 20
and a requirements draft that references March 28 — the ADR owner must reconcile.

## Alternatives considered
1. Opaque server-side tokens in Redis — rejected due to cross-region latency
2. Mutual TLS only — rejected; too heavy for mid-market partners

## Consequences
- Requires key rotation procedure and JWKS endpoint
- Security review is mandatory before flag default-on
- Observability: add metric `auth.jwt.issue_total` and alert on spike in `401 token_expired`

## Follow-ups (proposed, not yet assigned)
- Publish JWKS and document key rotation
- Reconcile the partner migration deadline across meeting notes, requirements, and this ADR
- Confirm whether cookie fallback ends March 18 or later
