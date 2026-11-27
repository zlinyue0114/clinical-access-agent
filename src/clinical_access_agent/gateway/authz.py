"""Care-team authorization.

A clinician may access a patient if and only if an active ``CareTeam`` whose subject
is that patient lists the clinician as a participant. The panel is read from the
FHIR server on every request; nothing is cached, so removing a clinician from a care
team takes effect immediately.

The server-side search is not trusted on its own: each returned care team is checked
again for status, participant and subject before it counts.
"""

from __future__ import annotations

from dataclasses import dataclass

from clinical_access_agent.gateway.fhir import FhirClient


@dataclass(frozen=True)
class Panel:
    clinician_id: str
    patient_ids: frozenset[str]


async def load_panel(fhir: FhirClient, clinician_id: str) -> Panel:
    member = f"Practitioner/{clinician_id}"
    teams = await fhir.search("CareTeam", {"participant": member})
    patients: set[str] = set()
    for team in teams:
        if team.get("status") != "active":
            continue
        members = {
            p.get("member", {}).get("reference")
            for p in team.get("participant", [])
            if isinstance(p, dict)
        }
        if member not in members:
            continue
        subject = team.get("subject", {}).get("reference", "")
        if isinstance(subject, str) and subject.startswith("Patient/"):
            patients.add(subject.removeprefix("Patient/"))
    return Panel(clinician_id=clinician_id, patient_ids=frozenset(patients))
