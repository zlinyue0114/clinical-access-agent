# 0004. Own seeded generator instead of Synthea

Status: accepted

## Context

The evaluation needs patients, care-team assignments and chest CT reports whose
ground truth is known exactly, plus prompt-injection text placed at controlled
positions. Synthea produces rich longitudinal records but no imaging report text,
no care-team panels in the shape the access model needs, and it adds a Java
toolchain to the build.

## Decision

Generate all records with a small, seeded Python generator in
`src/clinical_access_agent/data/`. Ground-truth labels are computed from the
structured findings before rendering, and are written to a separate file that never
enters the FHIR server.

## Consequences

- Identical seed, identical dataset; the manifest records a SHA-256 per output, and
  CI regenerates the committed sample and fails on any difference.
- Records are clinically shallow (no medication or encounter history). That is
  acceptable because the evaluated task only reads imaging reports and care teams.
- Replacing the generator later only requires producing the same bundle and label
  formats.
