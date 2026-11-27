# Threat model

This document lists what the system protects, from whom, where the trust
boundaries are, and which control and which test or evaluation result stands behind
each mitigation. Risks are mapped to the
[SAIF risk map](https://saif.google/secure-ai-framework/risks) and the
[OWASP Top 10 for Agentic Applications (2026)](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
at the end.

Scope: the code in this repository, run as described in the README, plus the
production configuration in [`production-eu.md`](production-eu.md) where a threat
depends on deployment. The system is a research prototype on synthetic data; it is
not intended for clinical use.

## System and trust boundaries

```
 clinician ──(1)── identity provider ── issues ui and agent tokens
     │
     │ question
     ▼
 agent runtime ──(2)── model provider (Vertex AI)   everything in the model
  │  guards          context leaves the operator's boundary here
  │  (agent token, held by the runtime)
  ▼
 EHR gateway ──(3)── FHIR record store             gateway holds service
  │  authz, de-id, pseudonyms, quotas              credentials; agent does not
  ▼
 audit log, follow-up worklist
```

| Boundary | Trust on each side |
|---|---|
| (1) clinician / identity provider | the identity provider authenticates the clinician; tokens are signed and short-lived |
| (2) agent / model | the model is untrusted for decisions about access; its context is treated as disclosed to the provider |
| (3) gateway / record store | the gateway is the only component with record-store credentials; the agent is an untrusted caller |

**Untrusted inputs.** The clinician's question; every report sentence (reports may
contain text written by people outside the care team: referral letters, outside
reports, transcription, a compromised upstream system); every model output.

## Assets

| ID | Asset |
|---|---|
| A1 | Patient identities and record content |
| A2 | Integrity of the record store |
| A3 | Correctness of follow-up decisions presented to the clinician |
| A4 | Audit log |
| A5 | Signing key of the token issuer, pseudonym key |
| A6 | Availability and model cost |

## Adversaries

| ID | Adversary | Capability |
|---|---|---|
| ADV1 | Author of injected record text | writes text that ends up in a report of a patient on the clinician's panel |
| ADV2 | Clinician exceeding their role | asks for patients outside their care teams, or for bulk data |
| ADV3 | Misbehaving model | follows injected instructions, hallucinates references, loops |
| ADV4 | Compromised dependency or tool | code running inside the agent or gateway process |
| ADV5 | Operator error | misconfiguration of regions, logging, keys or permissions |

ADV4 inside the gateway process defeats the gateway's controls; it is addressed only
by supply-chain controls (T11).

## Threats

Evidence refers to tests under `tests/` and to the evaluation in
[`evaluation.md`](evaluation.md). *Residual* states what the controls do not cover.

| ID | Threat | Adversary | Controls | Evidence | Residual |
|---|---|---|---|---|---|
| T1 | Instructions in record text steer the agent (indirect prompt injection) | ADV1, ADV3 | No capability to act on them: read-only tools, gateway authorization; screening; issued-reference check; record text wrapped as untrusted | `tests/agent/test_agent_run.py` layered scenarios; evaluation ablations | Screening recall 0.41 on held-out text; see T7 |
| T2 | Records of patients outside the care teams are read | ADV1, ADV2, ADV3 | Care-team check on every request, fail-closed; pseudonyms resolvable only within the panel; raw ids rejected; identical `404` for unknown and unauthorized | `tests/gateway/test_authorization.py` (every clinician × patient × report); evaluation: 0 out-of-panel reads in every configuration, 30 for the baseline | Care-team membership is the only rule; no break-glass, consent or role restrictions |
| T3 | Records are written, deleted or exported | ADV1, ADV3 | No write or export endpoint or tool; the one consequential action needs a hash-bound, single-use, expiring confirmation redeemed with a ui token; worklist kept outside the record store | `tests/gateway/test_api.py`, `tests/gateway/test_follow_up.py`; evaluation: 0 writes, 12 for the baseline | A clinician confirming a proposal they did not read (T15) |
| T4 | Acting as another clinician | ADV2, ADV3 | Identity only from a verified Ed25519 token; token held by the runtime, never in tool arguments or model context; fixed algorithm, issuer, audience, lifetime | `tests/gateway/test_components.py` (forged, expired, `none`, HS256 confusion); `test_identity_comes_from_the_token_not_from_the_request`; `test_model_never_sees_the_token_or_identities` | Development issuer signs any id; replaced by the hospital identity provider in production |
| T5 | Identifying data reaches the model provider | ADV5 | Allow-listed response fields; report text de-identified in the gateway; independent redaction in `after_tool`; per-clinician pseudonyms | `tests/gateway/test_deid.py`; evaluation: 0 identities in model context, 288 to 316 with both text layers off | Names of third parties without a title; free-text dates and places |
| T6 | Data leaves through the answer | ADV1, ADV3 | Nothing identifying is in the context to leak (T5); output redaction of identifier shapes and unissued references | evaluation: exfiltration success 0, baseline 1.00 | Clinical content of in-panel reports can be repeated, by design |
| T7 | A plausible "correction" in record text changes the follow-up decision | ADV1 | Screening (development corpus only); review flags; decision-support framing | evaluation: held-out tampering success 1.00 with the obedient model | **Open.** The text is indistinguishable from a legitimate addendum. Needs provenance of addenda and a rule that free text never downgrades a recommendation without review |
| T8 | Bulk access or enumeration from a compromised session | ADV2, ADV3 | Distinct-patient and request-rate quotas per clinician; tool-call budget per session; uniform denials | `tests/gateway/test_quota_limits.py` | Quotas are per process and must be sized above a full panel review |
| T9 | Audit records altered or removed | ADV5 | Hash chain; gateway refuses to start on a broken chain; response withheld if the record cannot be written; external head anchoring | `tests/gateway/test_components.py`, `test_tampered_audit_log_prevents_startup`, `test_unwritable_audit_log_withholds_the_response` | Truncation detectable only with an anchored head; single writer |
| T10 | Telemetry stores prompts and record text | ADV5 | Content capture forced off when an agent is built | `test_content_capture_is_disabled_after_building` | Other instrumentation added later must be reviewed |
| T11 | Compromised dependency, image or tool definition | ADV4 | Locked dependencies (`uv.lock`, `--locked` in CI); FHIR image pinned by digest; CI actions pinned to commits with Dependabot updates; fixed tool list, no dynamic tool discovery | `.github/workflows/ci.yml`, `.github/dependabot.yml` | No SBOM or agent bill of materials; dependency updates still need review |
| T12 | Runaway loops or cost (denial of wallet) | ADV1, ADV3 | 20 model calls and 300 tool calls per question; quotas; budget alert in the deployment | `test_model_calls_are_bounded`; `test_unlisted_tools_and_budget` | Token volume per call is not capped |
| T13 | Key compromise | ADV4, ADV5 | Keys outside the agent process; development keys git-ignored and created locally; production keys in KMS or the identity provider | `.gitignore`; `production-eu.md` | Rotation is not implemented |
| T14 | Record store unavailable or returns inconsistent data | ADV5 | Every upstream error denies; paging bounded; read responses checked for type and id | `tests/gateway/test_components.py` | None beyond availability |
| T15 | Clinicians over-trust the answer (automation bias) | ADV3 | Decision-support wording; review flags; confirmation shown in the clinician's own interface | none (no user study) | Unmeasured |

Not applicable in this design: code execution by the agent (no such tool), long-term
memory (sessions are not persisted), communication between agents (single agent),
and threats to model training or weights (the model is consumed as a service).

## Mapping to SAIF

| SAIF risk | Threats | Notes |
|---|---|---|
| Prompt Injection | T1, T7 | indirect, through record text |
| Sensitive Data Disclosure | T2, T5, T6, T10 | |
| Rogue Actions | T3, T8 | |
| Insecure Model Output | T6, T7 | output redaction; decision content not verifiable |
| Excessive Data Handling | T5, T8 | minimization and quotas |
| Insecure Integrated Component | T4, T11 | tools and gateway as integrated components |
| Denial of ML Service | T12 | |
| Inferred Sensitive Data | T5 | the model sees clinical content and may infer more; minimized, not eliminated |
| Data Poisoning, Unauthorized Training Data, Model Source Tampering, Model Exfiltration, Model Deployment Tampering, Model Reverse Engineering, Model Evasion | none | no model training or hosting in scope |

## Mapping to OWASP Top 10 for Agentic Applications (2026)

| Risk | Threats | Status |
|---|---|---|
| ASI01 Agent Goal Hijack | T1, T7 | access and actions contained; decision content open (T7) |
| ASI02 Tool Misuse and Exploitation | T2, T3, T8 | read-only tools, gateway checks, budgets |
| ASI03 Identity and Privilege Abuse | T2, T4 | identity from token; least privilege at the gateway |
| ASI04 Agentic Supply Chain Vulnerabilities | T11 | partial |
| ASI05 Unexpected Code Execution | none | no code execution capability |
| ASI06 Memory and Context Poisoning | T1 | no persistent memory; context poisoning as T1 |
| ASI07 Insecure Inter-Agent Communication | none | single agent |
| ASI08 Cascading Failures | T12, T14 | bounded calls, fail-closed upstream |
| ASI09 Human-Agent Trust Exploitation | T7, T15 | open |
| ASI10 Rogue Agents | T3, T8, T12 | no capability beyond reading and proposing |

## Review

Revisit this document when a tool, endpoint, data source, model, or deployment
region is added or changed, and re-run `make eval` and `make eval-live`.
