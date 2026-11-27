# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

## [0.1.0] - 2026-11-27

First complete version.

### Added

- Design specification with invariants I1 to I9 and success criteria.
- Seeded synthetic FHIR generator: patients, practitioners, care teams, bilingual
  (EN/DE) chest CT reports with identifying headers, follow-up ground truth, and a
  balanced prompt-injection corpus.
- EHR gateway as the security boundary: Ed25519 session tokens, care-team
  authorization on every request, uniform denials, per-clinician keyed pseudonyms,
  allow-listed responses, de-identification of report text, quotas, hash-chained
  audit log, confirmation-bound follow-up proposals.
- Google ADK agent with read-only tools, issued-reference check, record-text
  screening, identifier redaction, output redaction, bounded model and tool calls,
  and telemetry content capture disabled.
- Deterministic scripted model with an obedient mode for worst-case security tests.
- Evaluation harness with a held-out injection suite, layer ablations and a
  deliberately vulnerable baseline as positive control; results committed and
  regenerated in CI.
- Threat model mapped to SAIF and the OWASP Top 10 for Agentic Applications,
  regulatory notes (MDR, AI Act, GDPR), reporting checklist (TRIPOD+AI, TRIPOD-LLM,
  FUTURE-AI) and a target EU production configuration.
- Offline demo (`make demo`).

### Known limitations

- Clinical tampering through plausible record text is not prevented (threat T7).
- Injection screening generalizes poorly to unseen phrasings (0.41 recall on the
  held-out set).
- No evaluation with a live model or with clinicians in this release.

[0.1.0]: https://github.com/zlinyue0114/clinical-access-agent/releases/tag/v0.1.0
