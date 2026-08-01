# API Changes Require a Rollback Plan

**Standard ID:** ENG-STD-001  
**Applies to:** All services exposing HTTP/gRPC APIs

## Rule

Any change that alters a public or partner-facing API contract (request/response
shape, auth requirements, error codes, or versioning) **must** include a
documented rollback plan before merge.

## Required elements

1. Description of the breaking vs. additive nature of the change
2. Steps to revert traffic to the previous version within 15 minutes
3. Feature-flag or versioned-endpoint strategy when applicable
4. Owner responsible for executing the rollback

## Rationale

Unplanned API breakage has caused multi-hour customer incidents. A written
rollback plan is the minimum bar for production readiness of API changes.
