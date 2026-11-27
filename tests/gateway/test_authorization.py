"""Invariant I2: a clinician reads a patient if and only if they share a care team.

The access relation is computed directly from the generated bundle and compared with
the gateway's answer for every clinician and every patient and report.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from clinical_access_agent.data.memory_fhir import InMemoryFhir
from clinical_access_agent.gateway.pseudonym import Kind
from tests.gateway.support import Alias, Auth, World

NOT_FOUND = {"error": "not_found"}


def test_every_clinician_sees_exactly_their_panel(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    for clinician in world.clinicians:
        response = client.get("/v1/patients", headers=auth(clinician))
        assert response.status_code == 200
        seen = {item["patient"] for item in response.json()}
        expected = {alias(clinician, Kind.PATIENT, p) for p in world.panels[clinician]}
        assert seen == expected
        assert expected, "every clinician in the dataset has at least one patient"


def test_report_listing_matches_access_relation_exhaustively(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    checked = 0
    for clinician in world.clinicians:
        headers = auth(clinician)
        for patient in world.patients:
            ref = alias(clinician, Kind.PATIENT, patient)
            response = client.get(f"/v1/patients/{ref}/reports", headers=headers)
            if patient in world.panels[clinician]:
                assert response.status_code == 200
                listed = {r["report"] for r in response.json()}
                expected = {
                    alias(clinician, Kind.REPORT, r) for r in world.reports.get(patient, [])
                }
                assert listed == expected
            else:
                assert response.status_code == 404
                assert response.json() == NOT_FOUND
            checked += 1
    assert checked == len(world.clinicians) * len(world.patients)


def test_report_reading_matches_access_relation_exhaustively(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    for clinician in world.clinicians:
        headers = auth(clinician)
        for patient, reports in world.reports.items():
            p_ref = alias(clinician, Kind.PATIENT, patient)
            for report in reports:
                r_ref = alias(clinician, Kind.REPORT, report)
                response = client.get(f"/v1/patients/{p_ref}/reports/{r_ref}", headers=headers)
                expected = 200 if patient in world.panels[clinician] else 404
                assert response.status_code == expected, (clinician, patient, report)


def test_report_must_belong_to_the_named_patient(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    clinician = world.clinicians[0]
    own = sorted(p for p in world.panels[clinician] if world.reports.get(p))
    first, second = own[0], own[1]
    p_ref = alias(clinician, Kind.PATIENT, first)
    r_ref = alias(clinician, Kind.REPORT, world.reports[second][0])
    response = client.get(f"/v1/patients/{p_ref}/reports/{r_ref}", headers=auth(clinician))
    assert response.status_code == 404


def test_pseudonyms_are_not_transferable_between_clinicians(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    shared = [
        (a, b, p)
        for a in world.clinicians
        for b in world.clinicians
        if a < b
        for p in world.panels[a] & world.panels[b]
    ]
    assert shared, "the dataset contains patients on more than one care team"
    a, b, patient = shared[0]
    assert alias(a, Kind.PATIENT, patient) != alias(b, Kind.PATIENT, patient)
    response = client.get(
        f"/v1/patients/{alias(a, Kind.PATIENT, patient)}/reports", headers=auth(b)
    )
    assert response.status_code == 404


def test_raw_identifiers_are_never_accepted(client: TestClient, world: World, auth: Auth) -> None:
    clinician = world.clinicians[0]
    patient = sorted(world.panels[clinician])[0]
    mrn = world.resource("Patient", patient)["identifier"][0]["value"]
    attempts = [
        patient,
        mrn,
        f"Patient/{patient}",
        "p_" + "g" * 20,
        "p_" + "0" * 21,
        "..%2F..%2FPatient%2Fpat-0001",
    ]
    bodies = set()
    for ref in attempts:
        response = client.get(f"/v1/patients/{ref}/reports", headers=auth(clinician))
        assert response.status_code == 404, ref
        bodies.add(response.text)
    assert len(bodies) == 1, "denials must be indistinguishable"


def test_injection_targets_are_unreachable(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    attacks = [
        label
        for label in world.labels
        if label.injection_goal == "cross_patient_read" and label.injection_target_patient
    ]
    assert attacks
    for label in attacks:
        target = label.injection_target_patient
        assert target is not None
        readers = [c for c in world.clinicians if label.patient_id in world.panels[c]]
        for clinician in readers:
            for ref in (target, alias(clinician, Kind.PATIENT, target)):
                response = client.get(f"/v1/patients/{ref}/reports", headers=auth(clinician))
                assert response.status_code == 404


def test_removal_from_care_team_takes_effect_immediately(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    clinician = world.clinicians[0]
    patient = sorted(world.panels[clinician])[0]
    ref = alias(clinician, Kind.PATIENT, patient)
    assert client.get(f"/v1/patients/{ref}/reports", headers=auth(clinician)).status_code == 200

    member = f"Practitioner/{clinician}"
    for team in world.resources("CareTeam"):
        if team["subject"]["reference"] == f"Patient/{patient}":
            team["participant"] = [
                p for p in team["participant"] if p["member"]["reference"] != member
            ]

    assert client.get(f"/v1/patients/{ref}/reports", headers=auth(clinician)).status_code == 404


def test_inactive_care_teams_grant_nothing(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    clinician = world.clinicians[0]
    for team in world.resources("CareTeam"):
        team["status"] = "inactive"
    assert client.get("/v1/patients", headers=auth(clinician)).json() == []
    patient = sorted(world.panels[clinician])[0]
    ref = alias(clinician, Kind.PATIENT, patient)
    assert client.get(f"/v1/patients/{ref}/reports", headers=auth(clinician)).status_code == 404


def test_gateway_never_writes_to_the_record_store(
    client: TestClient, world: World, fhir: InMemoryFhir, auth: Auth, alias: Alias
) -> None:
    clinician = world.clinicians[0]
    client.get("/v1/patients", headers=auth(clinician))
    for patient in world.panels[clinician]:
        client.get(
            f"/v1/patients/{alias(clinician, Kind.PATIENT, patient)}/reports",
            headers=auth(clinician),
        )
    assert fhir.requests
    assert {r.method for r in fhir.requests} == {"GET"}
