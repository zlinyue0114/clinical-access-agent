# 0003. Local first, cloud production documented

Status: accepted

## Decision

Everything runs locally except model inference, which uses Gemini through Vertex
AI. Offline tests and CI use a deterministic stub model. The production deployment
on Google Cloud (agent identity, managed guardrails, EU residency policies) is
documented, not deployed.

## Consequences

- Reproducible at near-zero cost.
- Managed-service behavior is described, not demonstrated; the documentation states
  which local component each managed service would replace.
