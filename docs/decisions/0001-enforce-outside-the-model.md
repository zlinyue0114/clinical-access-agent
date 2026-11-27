# 0001. Enforce access control outside the model

Status: accepted

## Context

Prompt instructions and content classifiers can be bypassed. During exploratory
tests, a prompt-injection classifier detected explicit injections in English and
German but missed politely phrased, domain-plausible instructions. A memory-scoping
experiment showed that user-level isolation enforced only in application code is
bypassed as soon as the code passes the wrong identifier.

## Decision

All access decisions are made by the EHR gateway using identity from a verified
token. The agent never supplies the clinician identity, and patient identifiers it
supplies are always re-checked against the care-team panel. Classifiers and LLM
judges are used only as additional, measured layers.

## Consequences

- The agent can be evaluated as fully compromised without weakening the access claims.
- The gateway becomes the component that needs the most rigorous tests.
