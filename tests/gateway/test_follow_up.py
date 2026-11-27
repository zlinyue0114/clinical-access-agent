"""Invariant I3: the only consequential action needs a hash-bound, single-use,
expiring confirmation that only a ui token can redeem."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient

from clinical_access_agent.gateway.config import Settings
from clinical_access_agent.gateway.pseudonym import Kind
from clinical_access_agent.gateway.tokens import Audience
from tests.gateway.support import Alias, Auth, Clock, World


def _target(world: World) -> tuple[str, str, str]:
    clinician = world.clinicians[0]
    patient = next(p for p in sorted(world.panels[clinician]) if world.reports.get(p))
    return clinician, patient, world.reports[patient][0]


def _prepare(client: TestClient, auth: Auth, alias: Alias, world: World) -> dict[str, Any]:
    clinician, patient, report = _target(world)
    response = client.post(
        "/v1/actions/follow-up/prepare",
        json={
            "patient": alias(clinician, Kind.PATIENT, patient),
            "report": alias(clinician, Kind.REPORT, report),
            "interval_months": 6,
        },
        headers=auth(clinician),
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _commit(
    client: TestClient, auth: Auth, clinician: str, body: dict[str, Any], **kw: Any
) -> httpx.Response:
    audience = kw.pop("audience", Audience.UI)
    payload = {"confirmation_id": body["confirmation_id"], "digest": body["digest"], **kw}
    response: httpx.Response = client.post(
        "/v1/actions/follow-up/commit", json=payload, headers=auth(clinician, audience)
    )
    return response


def test_prepare_then_confirm_adds_one_worklist_entry(
    client: TestClient, world: World, auth: Auth, alias: Alias, settings: Settings
) -> None:
    clinician, patient, report = _target(world)
    prepared = _prepare(client, auth, alias, world)
    assert prepared["interval_months"] == 6
    assert len(prepared["digest"]) == 64

    committed = _commit(client, auth, clinician, prepared)
    assert committed.status_code == 200
    assert committed.json()["patient"] == alias(clinician, Kind.PATIENT, patient)
    assert committed.json()["report"] == alias(clinician, Kind.REPORT, report)

    lines = settings.worklist_path.read_text().splitlines()
    assert len(lines) == 1
    assert patient in lines[0]


def test_agent_tokens_cannot_confirm(
    client: TestClient, world: World, auth: Auth, alias: Alias, settings: Settings
) -> None:
    clinician, _, _ = _target(world)
    prepared = _prepare(client, auth, alias, world)
    response = _commit(client, auth, clinician, prepared, audience=Audience.AGENT)
    assert response.status_code == 403
    assert not settings.worklist_path.exists()
    assert _commit(client, auth, clinician, prepared).status_code == 200


def test_confirmation_is_single_use(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    clinician, _, _ = _target(world)
    prepared = _prepare(client, auth, alias, world)
    assert _commit(client, auth, clinician, prepared).status_code == 200
    again = _commit(client, auth, clinician, prepared)
    assert again.status_code == 409
    assert again.json() == {"error": "confirmation_invalid"}


def test_wrong_digest_burns_the_confirmation(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    clinician, _, _ = _target(world)
    prepared = _prepare(client, auth, alias, world)
    forged = {**prepared, "digest": "0" * 64}
    assert _commit(client, auth, clinician, forged).status_code == 409
    assert _commit(client, auth, clinician, prepared).status_code == 409


def test_confirmation_expires(
    client: TestClient, world: World, auth: Auth, alias: Alias, clock: Clock
) -> None:
    clinician, _, _ = _target(world)
    prepared = _prepare(client, auth, alias, world)
    clock.advance(5 * 60 + 1)
    assert _commit(client, auth, clinician, prepared).status_code == 409


def test_confirmation_belongs_to_one_clinician(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    prepared = _prepare(client, auth, alias, world)
    other = world.clinicians[1]
    assert _commit(client, auth, other, prepared).status_code == 409


def test_prepare_is_limited_to_the_panel(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    clinician = world.clinicians[0]
    outsider = next(
        p for p in sorted(set(world.patients) - world.panels[clinician]) if world.reports.get(p)
    )
    response = client.post(
        "/v1/actions/follow-up/prepare",
        json={
            "patient": alias(clinician, Kind.PATIENT, outsider),
            "report": alias(clinician, Kind.REPORT, world.reports[outsider][0]),
            "interval_months": 3,
        },
        headers=auth(clinician),
    )
    assert response.status_code == 404


def test_prepare_rejects_out_of_range_and_unknown_fields(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    clinician, patient, report = _target(world)
    base = {
        "patient": alias(clinician, Kind.PATIENT, patient),
        "report": alias(clinician, Kind.REPORT, report),
    }
    for extra in ({"interval_months": 99}, {"interval_months": -1}, {"interval_months": 3, "x": 1}):
        response = client.post(
            "/v1/actions/follow-up/prepare", json={**base, **extra}, headers=auth(clinician)
        )
        assert response.status_code == 400


def test_confirm_rechecks_access(
    client: TestClient, world: World, auth: Auth, alias: Alias, settings: Settings
) -> None:
    clinician, patient, _ = _target(world)
    prepared = _prepare(client, auth, alias, world)
    for team in world.resources("CareTeam"):
        if team["subject"]["reference"] == f"Patient/{patient}":
            team["status"] = "inactive"
    assert _commit(client, auth, clinician, prepared).status_code == 404
    assert not settings.worklist_path.exists()
