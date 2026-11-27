# Reporting against TRIPOD+AI, TRIPOD-LLM and FUTURE-AI

TRIPOD+AI and its LLM extension, TRIPOD-LLM, are reporting guidelines for studies
that develop or evaluate prediction models. This repository evaluates an LLM agent
on a classification task (does a report need follow-up imaging) using synthetic
data, so the guidelines apply in part. FUTURE-AI gives development and deployment
recommendations for trustworthy AI in healthcare under six principles.

The tables point to where each applicable topic is answered, and say plainly where
it is not. Item numbers are omitted on purpose; use the official checklists for a
submission.

## TRIPOD+AI and TRIPOD-LLM

| Topic | Status | Where |
|---|---|---|
| Intended use, users and setting | reported | README, [`agent.md`](agent.md), [`regulatory.md`](regulatory.md) |
| Data source and participants | reported, synthetic | [`data.md`](data.md): generator, panel and care-team structure, seed |
| Outcome definition and how it was labelled | reported | follow-up rules and label derivation in [`data.md`](data.md); labels computed from structured findings before rendering |
| Sample size | reported | 120 patients, 219 reports, 6 clinicians; no power calculation |
| Missing data | not applicable | the generator produces complete records |
| Model: identity, version, access route | reported | model name in each results file; `CAA_MODEL`; Vertex AI |
| Prompt and inference settings | reported | [`prompt.py`](../src/clinical_access_agent/agent/prompt.py), temperature 0, limits in [`agent.md`](agent.md) |
| Tools, retrieval and pre-processing of inputs | reported | [`gateway.md`](gateway.md) (minimization, de-identification), [`agent.md`](agent.md) (screening, redaction) |
| Data leakage between development and test | addressed | held-out injection set written after the screening rules were frozen; generated reports are new text, but their templates are in this public repository |
| Performance measures with definitions | reported | [`evaluation.md`](evaluation.md) metrics table |
| Uncertainty of estimates | not reported | single seed, deterministic scripted runs; confidence intervals and repeated live runs are future work |
| Human evaluation | not done | no clinician review of outputs |
| Fairness and subgroups | partial | screening recall by report language (EN/DE); no subgroup analysis of answer quality; outcomes do not depend on demographics in the generator |
| Harms and failure analysis | reported | security suites, ablations, open risks T7 and T15 in [`threat-model.md`](threat-model.md) |
| Limitations | reported | [`evaluation.md`](evaluation.md), [`gateway.md`](gateway.md) |
| Code and data availability | reported | this repository, Apache-2.0; data reproducible from seed; results regenerated in CI |
| Funding and conflicts | not applicable | none |

## FUTURE-AI

| Principle | Status | Where |
|---|---|---|
| Fairness | partial | identical access rules for every clinician; screening recall by language; no demographic analysis |
| Universality | partial | standard interfaces (FHIR R4, OAuth-style bearer tokens); bilingual reports; single synthetic site |
| Traceability | reported | per-request audit log with hash chain; versioned generator and results; model and prompt recorded |
| Usability | not evaluated | no user study; the confirmation and review-flag design is untested with clinicians |
| Robustness | reported | held-out injection suite, layer ablations, positive control, fail-closed behavior under upstream errors |
| Explainability | partial | each triage item carries a one-sentence rationale and the report reference; no evaluation of rationale quality |

## References

- Collins GS et al. TRIPOD+AI statement: updated guidance for reporting clinical
  prediction models that use regression or machine learning methods. BMJ
  2024;385:e078378. doi:10.1136/bmj-2023-078378
- Gallifant J et al. The TRIPOD-LLM reporting guideline for studies using large
  language models. Nat Med 2025;31:60-69. doi:10.1038/s41591-024-03425-5
- Lekadir K et al. FUTURE-AI: international consensus guideline for trustworthy and
  deployable artificial intelligence in healthcare. BMJ 2025;388:e081554.
  doi:10.1136/bmj-2024-081554
