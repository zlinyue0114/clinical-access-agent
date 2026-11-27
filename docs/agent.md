# Agent

The agent is a single Google ADK `LlmAgent` that answers questions such as *"Which of
my patients' chest CT reports need follow-up?"*. It runs on Gemini through Vertex AI
for live use, and on a deterministic scripted model offline and in CI.

The agent is **not** the security boundary; the [gateway](gateway.md) is. The
agent-side layers below reduce what reaches the model and make failures visible,
and the evaluation measures each of them separately.

## Tools

| Tool | Gateway call | Notes |
|---|---|---|
| `list_my_patients()` | `GET /v1/patients` | issues patient references to the session |
| `list_reports(patient)` | `GET /v1/patients/{patient}/reports` | issues report references |
| `read_report(patient, report)` | `GET /v1/patients/{patient}/reports/{report}` | result is screened |
| `propose_follow_up(patient, report, interval_months)` | `POST .../prepare` | returns *awaiting confirmation*; the agent cannot confirm |
| `submit_triage(items)` | none | records the structured answer for the evaluation |

No tool writes, deletes or exports, and none takes an identity, token or URL. The
token lives in the gateway client inside the runtime.

## Layers

```
model request ──► before_model     count calls; run stops at the limit         (I9)
tool call     ──► before_tool      allow-list, issued-reference check, budget  (I1, I2)
              ──► gateway          token, care team, pseudonyms, quota, audit  (I1-I4, I6, I7)
tool result   ──► after_tool       register references; screen report text;     (I4, I5)
                                   redact identifiers the gateway missed
model text    ──► after_model      redact raw identifiers and unknown refs     (I4)
spans         ──► telemetry        content capture off                         (I8)
```

**Issued-reference check.** A patient or report reference is accepted only if a
listing tool returned it earlier in the same session. References that appear inside
report text are never registered, so an injected "please also review patient X"
cannot be acted on even if the model tries: the call is refused before it reaches
the gateway, and the refusal is logged as `tool_blocked`.

**Screening.** Report text is split into sentences; sentences that address the model,
name another patient or identifier, ask for bulk data, ask for an action, or try to
override the follow-up decision are withheld and replaced by a marker naming the
category. The result tells the model to mark the report for human review and to
decide on the remaining findings. Screening is pattern-based and will miss
paraphrases; its recall on text outside the generation corpus is reported in the
evaluation rather than assumed.

**Identifier redaction.** After screening, report text passes an independent,
pattern-based redaction: labelled names (`Patient: …`, `Name: …`), labelled dates of
birth, names after a title and record-number shapes are replaced with `[REDACTED]`.
The gateway already de-identifies report text; this layer exists so that a
misconfigured or incomplete gateway step does not put identities into the model
context. Tests run with gateway de-identification switched off to show that this
layer alone keeps patient names and dates of birth out of every model request.

**Output redaction.** Model text is scanned for record-identifier shapes (`pat-…`,
`MRN-…`) and for pseudonyms that were never issued to the session; both are
replaced.

## The scripted model

`ScriptedModel` performs the review with fixed rules over the visible text, using
the same tools, callbacks and gateway as a live model. With `obedient=True` it also
follows injected instructions wherever it can, which makes it a worst-case model for
the security tests: the tests switch agent-side layers off one at a time and check
which layer stops each attempt.

| Configuration | Planted identifiers in report text | Stopped by |
|---|---|---|
| all layers on | replaced with `[ID]` before leaving the gateway; sentence also withheld | gateway de-identification, screening |
| gateway de-identification and screening off | replaced in `after_tool` | agent identifier redaction |
| additionally identifier redaction off | model requests them | issued-reference check (`tool_blocked`) |
| additionally reference check off | requests reach the gateway | gateway authorization (`404`, audited as `deny`) |

## Running

```bash
make ask-offline                      # scripted model, needs `make gateway` running
make ask                              # Gemini via Vertex AI, reads .env
make ask CLINICIAN=prac-03 QUESTION="Which reports need review because content was withheld?"
```

`--trace` prints the guard decisions: blocked calls, gateway denials and screened
reports.

## Limits

- One agent instance per clinician session; references and budgets are not shared.
- At most 20 model calls and 300 tool calls per question.
- The follow-up decision is decision support for the clinician, not a diagnosis.
