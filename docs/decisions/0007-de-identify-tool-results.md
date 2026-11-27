# 0007. De-identify tool results, not only user input

Status: accepted

## Context

A common pattern for PHI protection in agents redacts the user's message before the
model call (`before_model` or `before_agent` callbacks). In a clinical data agent
that protects the wrong path: the user rarely types identifiers, while every tool
result that returns record text does. In a prototype built on that pattern, names
and contact details returned by a tool reached the model unredacted.

## Decision

- Report text is de-identified in the gateway, before it leaves the security
  boundary, using the patient's own known identifiers plus identifier shapes.
- The agent applies an independent pattern-based redaction in `after_tool`, so a
  gap in one layer is not a leak.
- References stay pseudonymous and resolvable only by the gateway; re-identification
  is an explicit, audited ui-token request.
- The dataset contains identifying text on purpose, and tests include positive
  controls with each layer switched off.

## Consequences

- One extra `Patient` read per report request.
- Pattern-based steps miss unknown names without a title. A deployment should put a
  dedicated de-identification service behind the gateway step.
- Over-redaction is possible for surnames that are also ordinary words; matching is
  case-sensitive and whole-word to limit it, and a test checks that clinical
  sentences are unchanged across the dataset.
