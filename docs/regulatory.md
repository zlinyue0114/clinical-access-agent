# Regulatory notes (EU)

This repository is a research prototype on synthetic data. It is not a medical
device, has not been placed on the market and must not be used for patient care.
The notes below record how the design relates to the rules that would apply if a
system like this were deployed in an EU hospital, so that the engineering choices
can be read against them. They are an engineering reading, not legal advice.

Status of the law as of November 2026.

## Medical Device Regulation (EU) 2017/745

Software whose intended purpose is to provide information used to take decisions for
diagnostic or therapeutic purposes qualifies as medical device software
(MDCG 2019-11), and Rule 11 of Annex VIII classifies such software as at least
class IIa. A tool that tells a clinician which imaging reports need follow-up, and
at what interval, informs clinical management and would very likely qualify.
Class IIa and above require a notified body.

Consequences for the design, already reflected here:

- the intended purpose is decision support for the treating clinician, who makes the
  decision; the agent cannot order, change or cancel anything;
- the follow-up rules in [`data.md`](data.md) are a simplified demonstration of
  published guidance and are labelled as such; a product would need a clinical
  evaluation of its actual decision logic, which here is the model's.

## AI Act (EU) 2024/1689, as amended by Regulation (EU) 2026/1744

AI that is, or is a safety component of, a product under Annex I legislation that
requires third-party conformity assessment is high-risk (Article 6(1)). The MDR is in
Annex I, so medical device software that needs a notified body is a high-risk AI
system. The Digital Omnibus on AI (Regulation (EU) 2026/1744, in force since
27 July 2026) moved the application date of the high-risk obligations for Annex I
systems to 2 August 2028 and kept medical devices within scope.

How the high-risk requirements map to this repository:

| Requirement | Article | Where it is addressed |
|---|---|---|
| Risk management system | 9 | [`threat-model.md`](threat-model.md); evaluation with positive control; open risks stated (T7, T15) |
| Data and data governance | 10 | synthetic data only; generator, labels and their limits in [`data.md`](data.md) |
| Technical documentation | 11 | `DESIGN_SPEC.md`, `docs/`, decision records |
| Record-keeping (automatic logs) | 12 | hash-chained audit log of every gateway request ([`gateway.md`](gateway.md)) |
| Transparency and instructions for use | 13 | intended purpose and limits in the README and [`agent.md`](agent.md); answers framed as decision support |
| Human oversight | 14 | read-only agent; consequential action needs the clinician's confirmation in their own interface; review flags |
| Accuracy, robustness, cybersecurity | 15 | [`evaluation.md`](evaluation.md): quality metrics, injection suites, ablations; gateway as the security boundary |

Not covered: a quality management system (Article 17), post-market monitoring, and
conformity assessment. The hospital as deployer would have its own obligations
(Article 26), including human oversight by trained staff and keeping the logs.

The general-purpose model behind the agent is subject to the obligations for GPAI
providers, which rest with the model provider.

## GDPR (EU) 2016/679

Health data is a special category (Article 9(1)). In a deployment, processing for
the treating clinician would rest on Article 9(2)(h) together with national law.

| Principle or duty | Article | Design response |
|---|---|---|
| Data minimization | 5(1)(c) | response allow-lists; de-identified report text; pseudonyms; the model never receives names, dates of birth or record numbers |
| Purpose limitation | 5(1)(b) | access scoped to the clinician's care teams; no bulk endpoints |
| Integrity and confidentiality | 5(1)(f), 32 | token authentication, fail-closed authorization, audit log, keys outside the agent |
| Data protection by design and by default | 25 | enforcement outside the model ([ADR 0001](decisions/0001-enforce-outside-the-model.md)); de-identification of tool results ([ADR 0007](decisions/0007-de-identify-tool-results.md)) |
| Data protection impact assessment | 35 | would be required: special-category data processed with new technology; the threat model is a technical input to it |
| Automated individual decisions | 22 | not solely automated; the clinician decides |
| Processors and transfers | 28, Chapter V | the model provider processes the model context; see region and endpoint choice in [`production-eu.md`](production-eu.md) |
| Records of processing | 30 | audit log supports, does not replace, the controller's records |

In Germany, state hospital laws and the professional secrecy duty under section 203
of the Criminal Code add conditions for involving external service providers such
as a cloud model provider. These belong in the DPIA and the processor contract.

## References

- Regulation (EU) 2017/745 on medical devices, Annex VIII Rule 11; MDCG 2019-11 on
  qualification and classification of software.
- Regulation (EU) 2024/1689 (AI Act), https://eur-lex.europa.eu/eli/reg/2024/1689/oj
- Regulation (EU) 2026/1744 (Digital Omnibus on AI), published in the Official Journal
  on 24 July 2026.
- Regulation (EU) 2016/679 (GDPR), https://eur-lex.europa.eu/eli/reg/2016/679/oj
