"""The gateway against a real HAPI FHIR server.

Requires ``make fhir-up data load``. Run with ``make test-integration``; skipped
otherwise. The expected access relation is computed from the loaded bundle file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from clinical_access_agent.gateway.app import create_app
from clinical_access_agent.gateway.config import Settings
from clinical_access_agent.gateway.pseudonym import Kind, Pseudonymizer
from clinical_access_agent.gateway.tokens import Audience, generate_keypair, issue

pytestmark = pytest.mark.integration

FHIR_BASE = os.environ.get("CAA_FHIR_BASE", "http://127.0.0.1:8090/fhir")
BUNDLE = Path(os.environ.get("CAA_BUNDLE", "data/generated/bundle.json"))
KEY = bytes(range(32))


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self.methods: set[str] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.methods.add(request.method)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@pytest.fixture(scope="module")
def bundle() -> dict[str, Any]:
    if not BUNDLE.exists():
        pytest.skip(f"{BUNDLE} not found; run `make data load`")
    try:
        httpx.get(f"{FHIR_BASE}/metadata", timeout=5).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"no FHIR server at {FHIR_BASE}; run `make fhir-up`")
    data: dict[str, Any] = json.loads(BUNDLE.read_text())
    return data


def test_access_relation_on_hapi(bundle: dict[str, Any], tmp_path: Path) -> None:
    resources = [e["resource"] for e in bundle["entry"]]
    clinicians = sorted(r["id"] for r in resources if r["resourceType"] == "Practitioner")
    patients = sorted(r["id"] for r in resources if r["resourceType"] == "Patient")
    panels: dict[str, set[str]] = {c: set() for c in clinicians}
    for team in (r for r in resources if r["resourceType"] == "CareTeam"):
        for member in team["participant"]:
            clinician = member["member"]["reference"].removeprefix("Practitioner/")
            panels[clinician].add(team["subject"]["reference"].removeprefix("Patient/"))

    private, public = generate_keypair()
    settings = Settings(
        fhir_base=FHIR_BASE,
        token_public_key=public,
        pseudonym_key=KEY,
        audit_path=tmp_path / "audit.jsonl",
        worklist_path=tmp_path / "worklist.jsonl",
        max_requests_per_minute=1_000_000,
        max_patients_per_hour=1_000_000,
    )
    transport = RecordingTransport()
    alias = Pseudonymizer(KEY).pseudonym

    with TestClient(create_app(settings, transport=transport)) as client:
        for clinician in clinicians:
            headers = {"Authorization": f"Bearer {issue(private, clinician, Audience.AGENT)}"}
            listed = client.get("/v1/patients", headers=headers)
            assert listed.status_code == 200
            assert {p["patient"] for p in listed.json()} == {
                alias(clinician, Kind.PATIENT, p) for p in panels[clinician]
            }
            for patient in patients:
                ref = alias(clinician, Kind.PATIENT, patient)
                response = client.get(f"/v1/patients/{ref}/reports", headers=headers)
                assert response.status_code == (200 if patient in panels[clinician] else 404)

    assert transport.methods == {"GET"}
