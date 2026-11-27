# 0002. Local HAPI FHIR server as the record store

Status: accepted

## Decision

Synthetic records are served by a HAPI FHIR server in Docker. The gateway talks to
it over the standard FHIR REST API.

## Consequences

- Realistic, standards-based data access; no cloud dependency for data.
- Reviewers need Docker. The image is pinned by digest in `docker-compose.yml`, so
  every checkout runs the same server build. To upgrade, pull the new image, run
  `make fhir-up data load` and the integration tests, then replace the digest.
