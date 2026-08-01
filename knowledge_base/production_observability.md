# Production Changes Require Observability

**Standard ID:** SRE-STD-005  
**Applies to:** Deployments, feature launches, infrastructure changes

## Rule

No production change ships without:

1. At least one **metric** or dashboard panel that would show the change working
2. An **alert** or on-call note for the failure mode most likely to occur
3. A brief **runbook** link or rollback pointer in the change description

## Rationale

Silent launches delay detection. Observability is part of the change, not an
optional follow-up.
