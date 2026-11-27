# Production configuration for an EU deployment

This is a documented target configuration on Google Cloud, not a deployed one. It
lists what changes between the local setup in this repository and a deployment
that handles real patient data in the EU, and why. Several items come from
problems met while prototyping on the same platform; they are marked *lesson*.

## Residency covers storage, inference and telemetry

Data residency is not one setting. Stored data, model inference and logs and traces
each have their own location, and all three hold patient data.

| What | Setting |
|---|---|
| Record store | Cloud Healthcare API FHIR store in an EU region |
| Gateway, agent runtime | Cloud Run or Agent Runtime in the same EU region |
| Model inference | a regional or EU multi-region Vertex AI endpoint for a model listed on the [data residency page](https://cloud.google.com/vertex-ai/generative-ai/docs/learn/data-residency) |
| Logs, traces, audit log | Cloud Logging buckets and trace storage in the EU |
| Keys | Cloud KMS key rings in the same region |

*Lesson:* the default model in this repository is served only from the `global`
endpoint, which gives no guarantee about where processing happens. Residency
therefore constrains the model choice. Pick a model offered in an EU location,
set `GOOGLE_CLOUD_LOCATION` to that location, and re-run `make eval-live` with it
before switching: answer quality is model-specific. A stale `GOOGLE_CLOUD_LOCATION`
in a shell silently overrides `.env`; set it explicitly in the deployment, not in
developer shells.

## Organization policies

Enforced at the organization or folder, so that a project-level script cannot
undo them:

| Constraint | Value | Purpose |
|---|---|---|
| `gcp.resourceLocations` | `in:eu-locations` | stored resources stay in the EU |
| `gcp.restrictEndpointUsage` | deny non-EU and global inference endpoints | inference stays in the EU |
| `gcp.restrictNonCmekServices` | require CMEK for storage services | encryption keys under the hospital's control; deleting a key makes its data unreadable |
| `iam.disableServiceAccountKeyCreation` | enforced | no long-lived service account keys |

*Lesson:* these must be designed in from the start. Applied to an existing project,
blocking the global endpoint breaks every agent pinned to a global-only model, and
requiring CMEK breaks services created without it.

## Identity and access

- **Clinician tokens** come from the hospital identity provider, not from
  `caa-token`. The gateway already verifies issuer, audience, lifetime and an
  asymmetric signature; only the trusted public key and issuer change.
- **Agent identity**: the agent runtime runs under its own identity with no access to
  the record store. Only the gateway's identity can read the FHIR store, and only
  read.
- **Delegation**: the agent token represents the signed-in clinician for one session.
  It never enters tool arguments or the model context.
- **Least privilege in CI**: a dedicated deployment identity whose permitted role
  grants are restricted by IAM conditions; no admin roles on default service
  accounts. *Lesson:* a tutorial deployment script that grants broad admin roles to a
  default compute identity leaves an escalation path that cleanup does not remove.
- **Tool resolution**: tools are fixed in code and bound by name; no discovery of
  tool servers by search at runtime. *Lesson:* resolving a tool server by a
  substring search returned a look-alike registration first, and the credential would
  have been sent to it.

## Secrets and keys

| Key | Location | Note |
|---|---|---|
| Pseudonym key | Cloud KMS HMAC key or Secret Manager | never in the agent's environment |
| Token verification key | the identity provider's published keys | the gateway holds no signing key |
| Audit head anchor | written periodically to a separate, write-once bucket | detects truncation of the audit log |

## Logging and telemetry

- Audit log: append-only sink to a Cloud Logging bucket with a retention lock, plus
  periodic export of the head hash.
- Trace and log content capture stays off (`telemetry.py`). *Lesson:* switching the
  instrumentation library can silently start recording prompts and tool results on
  spans; re-check after any observability change.
- Data access audit logs for the FHIR store and Vertex AI enabled.
- Access to traces and logs restricted to operations staff; retention set to the
  shortest period the hospital's policy allows.

## Guardrails

- **Fail closed.** Any guardrail service in the request path (for example Model
  Armor in front of the model) is configured to block when unavailable. *Lesson:*
  such services can default to fail-open.
- **Screening services are an additional layer only.** *Lesson:* in a test, an
  explicit English injection was flagged, while a polite, clinically plausible German
  request to export patient data was not; the same text phrased explicitly was. The
  gateway remains the boundary.
- **De-identification service** behind the gateway's own step for names it cannot
  know (Cloud DLP de-identification with a deterministic, reversible transformation
  if clinicians need re-identification).

## Cost and availability

- Per-question bounds as in the repository (model calls, tool calls), billing labels
  per agent, and a budget with alerts on the project.
- Per-clinician quotas sized above a full panel review, alerting on sustained use
  near the limit, which is how bulk extraction would look.

## Before go-live

- DPIA, processor agreements, and the professional-secrecy conditions for external
  service providers ([`regulatory.md`](regulatory.md)).
- Live evaluation on the production model and region, including the held-out
  injection suite, and a clinician review of a sample of answers.
- A decision on the open risks T7 (tampering by plausible text) and T15 (automation
  bias) in [`threat-model.md`](threat-model.md).
