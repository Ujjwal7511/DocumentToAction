# Data Retention and PII Handling

**Standard ID:** DATA-STD-004  
**Applies to:** Features that store, process, or export personal data

## Rule

Features that collect or process personally identifiable information (PII) must:

1. Document the data elements collected and their retention period
2. Provide a deletion / export path consistent with policy
3. Avoid putting PII in application logs, analytics event payloads, or error traces
4. Note the legal basis or internal policy reference for processing

## Rationale

Undocumented PII flows create compliance and customer-trust risk. Retention and
deletion must be designed with the feature, not added later.
