# Security Review Required for Authentication Changes

**Standard ID:** SEC-STD-002  
**Applies to:** Auth, SSO, session, token, and permission systems

## Rule

Any modification to authentication, authorization, session management, password
reset, MFA, or role/permission logic **requires** a security review sign-off
before production deployment.

## Required elements

1. Threat model notes for the change
2. Review by a designated security reviewer (or security champion)
3. Confirmation that secrets are not logged and tokens are rotated on privilege change
4. Test evidence for privilege escalation and session fixation cases

## Rationale

Auth regressions are high-severity. Informal peer review alone is insufficient
for identity-related changes.
