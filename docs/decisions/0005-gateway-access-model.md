# 0005. Gateway access model: resolve-by-recomputation pseudonyms, uniform denials

Status: accepted

## Context

The agent must refer to patients and reports without seeing their identifiers, and
a reference must never become a way to reach a record outside the clinician's care
team, including references planted in report text by an attacker.

## Decision

- References are per-clinician HMAC pseudonyms. They are not encrypted ids and
  cannot be decoded. The gateway resolves a reference by recomputing the pseudonyms
  of the clinician's current panel and looking it up. Resolution and authorization
  are the same step.
- A reference that does not resolve, for any reason, returns the same `404` body.
- Report references are resolved only within the named patient, so a report
  reference cannot be paired with a different patient.
- The only consequential action, adding a follow-up entry, is prepared by the agent
  and confirmed with a separate ui token. The record store is never written.

## Consequences

- A planted raw id, record number or another clinician's pseudonym is denied without
  any special handling, and the attempt is audited.
- Each request costs one care-team search plus one resource search. That is
  acceptable for panel sizes in the tens to low hundreds; larger panels would need a
  pseudonym index kept by the gateway.
- Sharing a pseudonym between two clinicians has no effect, which keeps sessions
  unlinkable but means a handover has to go through the re-identification endpoint.
