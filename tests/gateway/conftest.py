from __future__ import annotations

import copy
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clinical_access_agent.data.generate import generate
from clinical_access_agent.data.memory_fhir import InMemoryFhir
from clinical_access_agent.gateway.app import create_app
from clinical_access_agent.gateway.config import Settings
from clinical_access_agent.gateway.pseudonym import Pseudonymizer
from clinical_access_agent.gateway.tokens import Audience, generate_keypair, issue
from tests.gateway.support import Alias, Auth, Clock, MakeClient, World

PSEUDONYM_KEY = bytes(range(32))
FHIR_BASE = "http://fhir.test/fhir"


@pytest.fixture(scope="session")
def base_world() -> World:
    bundle, labels = generate(seed=7, patient_count=60)
    world = World(bundle=bundle, labels=labels)
    world.clinicians = sorted(r["id"] for r in world.resources("Practitioner"))
    world.patients = sorted(r["id"] for r in world.resources("Patient"))
    world.panels = {c: set() for c in world.clinicians}
    for team in world.resources("CareTeam"):
        patient = team["subject"]["reference"].removeprefix("Patient/")
        for participant in team["participant"]:
            world.panels[participant["member"]["reference"].removeprefix("Practitioner/")].add(
                patient
            )
    for report in world.resources("DiagnosticReport"):
        patient = report["subject"]["reference"].removeprefix("Patient/")
        world.reports.setdefault(patient, []).append(report["id"])
    return world


@pytest.fixture
def world(base_world: World) -> World:
    """A copy per test, so tests may mutate the FHIR data."""
    return copy.deepcopy(base_world)


@pytest.fixture(scope="session")
def keys() -> tuple[bytes, bytes]:
    return generate_keypair()


@pytest.fixture
def pseudonyms() -> Pseudonymizer:
    return Pseudonymizer(PSEUDONYM_KEY)


@pytest.fixture
def clock() -> Clock:
    return Clock(wall=datetime.now(UTC))


@pytest.fixture
def fhir(world: World) -> InMemoryFhir:
    return InMemoryFhir(world.bundle)


@pytest.fixture
def settings(tmp_path: Path, keys: tuple[bytes, bytes]) -> Settings:
    return Settings(
        fhir_base=FHIR_BASE,
        token_public_key=keys[1],
        pseudonym_key=PSEUDONYM_KEY,
        audit_path=tmp_path / "audit" / "gateway.jsonl",
        worklist_path=tmp_path / "audit" / "worklist.jsonl",
        max_requests_per_minute=100_000,
        max_patients_per_hour=100_000,
    )


@pytest.fixture
def make_client(settings: Settings, fhir: InMemoryFhir, clock: Clock) -> Iterator[MakeClient]:
    clients: list[TestClient] = []

    def make(**overrides: Any) -> TestClient:
        app = create_app(
            Settings(**{**settings.__dict__, **overrides}),
            transport=fhir.transport(),
            now=clock.now,
            monotonic=clock.monotonic,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: MakeClient) -> TestClient:
    return make_client()


@pytest.fixture
def auth(keys: tuple[bytes, bytes]) -> Auth:
    def headers(clinician: str, audience: Audience = Audience.AGENT) -> dict[str, str]:
        return {"Authorization": f"Bearer {issue(keys[0], clinician, audience)}"}

    return headers


@pytest.fixture
def alias(pseudonyms: Pseudonymizer) -> Alias:
    return pseudonyms.pseudonym
