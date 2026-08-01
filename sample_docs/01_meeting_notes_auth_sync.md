# Sprint Sync — Auth Service Redesign
**Date:** 2026-03-10  
**Attendees:** Maya Chen (Eng), Priya Nair (PM), Jordan Lee (Security), Sam Ortiz (SRE)

## Agenda
1. Status of auth token migration
2. Deadline for partner API v2
3. Open risks

## Discussion

Maya reported that the new JWT issuance path is feature-flagged in staging.
The team agreed to keep the legacy session cookies until the partner rollout completes.

Priya stated the partner API v2 launch target is **Friday, March 20**.
Jordan reminded the group that any auth change needs a security review before production.

## Decisions
- We decided to ship JWT issuance behind the `auth_jwt_v2` flag.
- We agreed to retain cookie sessions through the end of March as a fallback.

## Risks
- Risk: Partner sandbox still rejects refreshed tokens when the clock skew exceeds 30 seconds.
- Concern: No rollback drill has been scheduled for the API cutover.

## Action items
- ACTION: Maya to complete JWT staging soak — due Wednesday
- TODO: Jordan to schedule security review for auth changes
- Follow-up: Sam to draft rollback plan for partner API v2

## Open questions
- Do we need customer communication before disabling cookie sessions?
- Who owns the partner FAQ update?
