# 0006. Agent-side guards are defence in depth, measured separately

Status: accepted

## Context

Prompt injection through record text cannot be ruled out by instructions to the
model or by pattern matching. If the agent were the security boundary, every new
paraphrase would be a potential breach.

## Decision

- The gateway remains the only boundary that the success criteria depend on
  ([ADR 0001](0001-enforce-outside-the-model.md)).
- The agent adds three deterministic layers: an issued-reference check before each
  tool call, screening of record text after each read, and redaction of model output.
- Each layer can be switched off in tests and evaluations, and each refusal is
  logged with the layer that produced it, so its contribution is measured, not
  assumed.
- Offline tests use a scripted model that, in obedient mode, follows injected
  instructions. Security tests do not depend on a live model behaving well.

## Consequences

- Breaking screening with a new paraphrase degrades the agent's answer quality and
  review flags, not access control.
- The issued-reference check makes some legitimate requests impossible, for example
  a clinician typing a reference copied from another session. The agent has to list
  the panel first; this costs one tool call.
- Screening may withhold legitimate sentences. The evaluation reports its false
  positive rate on clean reports.
