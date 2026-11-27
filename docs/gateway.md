# EHR gateway

The gateway is the security boundary of the system. The agent never talks to the
FHIR server; it calls the gateway, and the gateway decides what the agent may see.
Every decision depends on three inputs only:

1. the clinician identity from a verified, signed token;
2. care-team membership as recorded in the FHIR server;
3. a pseudonymous reference that the gateway itself issued.

Nothing the model writes into a tool argument can change any of the three.

## Endpoints

| Method and path | Returns | Token audience |
|---|---|---|
| `GET /healthz` | liveness | none |
| `GET /v1/patients` | the clinician's panel: pseudonym, sex, age band, language | agent, ui |
| `GET /v1/patients/{patient}/reports` | report pseudonym, date, title, status | agent, ui |
| `GET /v1/patients/{patient}/reports/{report}` | report text, wrapped as untrusted content | agent, ui |
| `GET /v1/patients/{patient}/identity` | name and record number | ui |
| `POST /v1/actions/follow-up/prepare` | a confirmation id and digest, valid 5 minutes | agent, ui |
| `POST /v1/actions/follow-up/commit` | the new worklist entry | ui |

There is no endpoint that writes to the record store or exports data. Other methods
on these paths return `405`; other paths return `404`.

Errors carry only a reason code, for example `{"error": "not_found"}`.

## How each invariant is enforced

| Invariant | Mechanism | Code | Tests |
|---|---|---|---|
| I1 identity from the token | Ed25519 JWT, fixed algorithm, issuer, audience, lifetime and subject checks; identity is never read from parameters or headers other than `Authorization` | `tokens.py`, `app.py` | `test_components.py`, `test_api.py` |
| I2 care-team access, fail-closed | panel loaded from `CareTeam` on every request and re-checked locally; any upstream doubt denies; unknown and unauthorized look identical | `authz.py`, `service.py`, `fhir.py` | `test_authorization.py` (exhaustive over clinicians, patients and reports) |
| I3 no write or export, confirmed action only | read-only routes; follow-up entries need a hash-bound, single-use, expiring confirmation redeemed with a ui token; access re-checked at commit | `actions.py` | `test_follow_up.py` |
| I4 minimization, de-identification, pseudonyms | allow-listed response models; report text de-identified before it leaves the gateway; per-clinician HMAC pseudonyms; key held by the gateway | `models.py`, `deid.py`, `pseudonym.py` | `test_api.py`, `test_deid.py`, `test_components.py` |
| I5 untrusted record content | report text returned inside `{"trust": "untrusted", ...}`; screening itself happens in the agent (M3) | `models.py` | `test_api.py` |
| I6 complete, tamper-evident audit | one record per request from a middleware, written before the response leaves; hash chain; startup refused on a broken log; response withheld if the record cannot be written | `audit.py`, `app.py` | `test_api.py`, `test_components.py` |
| I7 quotas | sliding windows for requests per minute and distinct patients per hour | `quota.py` | `test_quota_limits.py` |

## De-identification of report text

Structured fields are dropped by the response models, but report text carries its
own identifiers: real reports start with a header naming the patient, and some
mention the patient in the impression. Before a report leaves the gateway:

1. the patient's own name parts, record numbers and date of birth (ISO, German,
   British and US layouts, month names in English and German) are replaced with
   `[NAME]`, `[MRN]`, `[PATIENT_ID]` and `[DOB]`;
2. any remaining identifier shape (`MRN-123456`, `pat-0042`) becomes `[ID]`, labelled
   dates of birth become `[DOB]`, and names after a title (*Mr*, *Frau*, *Dr*) become
   `[NAME]`.

Step 2 also removes identifiers of other patients planted in the text, so an
injected "review patient MRN-123456" no longer names anyone. Findings, sizes and
recommendations are untouched; a test compares every clinical sentence before and
after. The agent applies an independent pattern-based pass on top
([`agent.md`](agent.md)).

This follows a lesson from building the prototype: de-identifying only what the user
types is not enough, because in a clinical agent patient data arrives through tool
results.

## Pseudonyms

`p_` and `r_` references are `HMAC-SHA256(key, kind | clinician | id)`, truncated to
80 bits. They are stable for one clinician and differ between clinicians, so two
sessions cannot correlate patients. The gateway resolves a reference by recomputing
the pseudonyms of the clinician's own panel; a reference to anyone else, a raw FHIR
id, a record number or another clinician's pseudonym does not resolve and returns
the same `404`.

The prompt-injection corpus names other patients by their raw FHIR id. Such a
request is denied by construction and appears in the audit log as
`deny / not_found` with the requested reference.

## Audit record

```json
{"seq": 4, "ts": "...", "request_id": "...", "clinician": "prac-01", "audience": "agent",
 "method": "GET", "route": "/v1/patients/{patient}/reports", "action": "reports.list",
 "resource": "pat-0042", "patient": null, "status": 404, "decision": "deny",
 "reason": "not_found", "prev": "<hash of record 3>", "hash": "<sha256 of this record>"}
```

`resource` is the reference as requested; `patient` is the resolved patient id, set
only when resolution succeeded. Report text and names are never logged.

```bash
uv run caa-audit verify audit/gateway.jsonl
uv run caa-audit verify audit/gateway.jsonl --expect-head <hash kept elsewhere>
```

## Running locally

```bash
make dev-keys     # .secrets/: issuer key pair and pseudonym key (git-ignored)
make fhir-up data load
make gateway      # http://127.0.0.1:8100
TOKEN=$(uv run caa-token issue --clinician prac-01 --aud agent)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8100/v1/patients
make test-integration
```

Settings are environment variables, documented in
[`config.py`](../src/clinical_access_agent/gateway/config.py).

## Known limitations

- Free-text de-identification knows the patient's own identifiers and common
  layouts. Names of other people are removed only after a title. A deployment should
  add a dedicated de-identification service (for example Cloud DLP) behind this step.

- Quotas and pending confirmations live in process memory: one gateway instance,
  reset on restart.
- Truncating the end of the audit log is only detectable against a head hash kept
  outside the log.
- The development issuer signs any clinician id it is given. In a deployment the
  hospital identity provider issues tokens and this tool is not used.
- Care-team membership is the only access rule. Break-glass access, consent
  directives and role-based restrictions are out of scope.
