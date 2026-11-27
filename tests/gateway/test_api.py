"""Authentication, response minimization, the closed API surface and failure modes."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clinical_access_agent.data.memory_fhir import InMemoryFhir
from clinical_access_agent.gateway.app import create_app
from clinical_access_agent.gateway.audit import AuditIntegrityError, verify
from clinical_access_agent.gateway.config import Settings
from clinical_access_agent.gateway.models import UNTRUSTED_NOTE
from clinical_access_agent.gateway.pseudonym import Kind
from clinical_access_agent.gateway.tokens import Audience, generate_keypair, issue
from tests.gateway.support import Alias, Auth, MakeClient, World


def _records(settings: Settings) -> list[dict[str, object]]:
    return [json.loads(line) for line in settings.audit_path.read_text().splitlines()]


def _sensitive_values(world: World) -> set[str]:
    values: set[str] = set()
    for patient in world.resources("Patient"):
        values.add(patient["birthDate"])
        values.update(i["value"] for i in patient["identifier"])
        for name in patient["name"]:
            values.add(name["family"])
    return values


# Authentication -------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer not-a-token", "Basic dXNlcjpwYXNz", "bearer  "],
)
def test_requests_without_a_valid_token_are_rejected(
    client: TestClient, header: str | None
) -> None:
    headers = {} if header is None else {"Authorization": header}
    response = client.get("/v1/patients", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"error": "invalid_token"}


def test_token_signed_by_another_key_is_rejected(client: TestClient, world: World) -> None:
    foreign_private, _ = generate_keypair()
    token = issue(foreign_private, world.clinicians[0], Audience.AGENT)
    response = client.get("/v1/patients", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_identity_comes_from_the_token_not_from_the_request(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    first, second = world.clinicians[0], world.clinicians[-1]
    only_second = sorted(world.panels[second] - world.panels[first])[0]
    ref = alias(second, Kind.PATIENT, only_second)
    response = client.get(
        f"/v1/patients/{ref}/reports",
        params={"clinician": second, "sub": second},
        headers={**auth(first), "X-Clinician-Id": second},
    )
    assert response.status_code == 404


# Minimization ----------------------------------------------------------------------


def test_patient_list_contains_only_allow_listed_fields(
    client: TestClient, world: World, auth: Auth
) -> None:
    response = client.get("/v1/patients", headers=auth(world.clinicians[0]))
    assert response.status_code == 200
    for item in response.json():
        assert set(item) == {"patient", "sex", "age_band", "language"}
    for value in _sensitive_values(world):
        assert value not in response.text


def test_report_text_is_returned_as_untrusted_content(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    injected = next(label for label in world.labels if label.injection_id)
    clinician = next(c for c in world.clinicians if injected.patient_id in world.panels[c])
    p_ref = alias(clinician, Kind.PATIENT, injected.patient_id)
    r_ref = alias(clinician, Kind.REPORT, injected.report_id)
    response = client.get(f"/v1/patients/{p_ref}/reports/{r_ref}", headers=auth(clinician))
    assert response.status_code == 200
    body = response.json()
    assert body["content"]["trust"] == "untrusted"
    assert body["content"]["note"] == UNTRUSTED_NOTE
    source = world.resource("DiagnosticReport", injected.report_id)
    assert body["content"]["text"] == base64.b64decode(source["presentedForm"][0]["data"]).decode()
    assert set(body) == {"report", "patient", "date", "title", "status", "language", "content"}
    for value in _sensitive_values(world):
        assert value not in response.text


def test_reidentification_requires_a_ui_token(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    clinician = world.clinicians[0]
    patient = sorted(world.panels[clinician])[0]
    ref = alias(clinician, Kind.PATIENT, patient)

    denied = client.get(f"/v1/patients/{ref}/identity", headers=auth(clinician))
    assert denied.status_code == 403
    assert denied.json() == {"error": "wrong_audience"}

    allowed = client.get(f"/v1/patients/{ref}/identity", headers=auth(clinician, Audience.UI))
    assert allowed.status_code == 200
    resource = world.resource("Patient", patient)
    assert allowed.json()["mrn"] == resource["identifier"][0]["value"]
    assert resource["name"][0]["family"] in allowed.json()["name"]


def test_reidentification_is_limited_to_the_panel(
    client: TestClient, world: World, auth: Auth, alias: Alias
) -> None:
    clinician = world.clinicians[0]
    outsider = sorted(set(world.patients) - world.panels[clinician])[0]
    ref = alias(clinician, Kind.PATIENT, outsider)
    response = client.get(f"/v1/patients/{ref}/identity", headers=auth(clinician, Audience.UI))
    assert response.status_code == 404


# Closed surface ---------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "path", ["/v1/patients", "/v1/patients/p_x/reports", "/v1/patients/p_x/reports/r_x"]
)
def test_record_endpoints_are_read_only(
    client: TestClient, world: World, auth: Auth, method: str, path: str
) -> None:
    response = client.request(method, path, headers=auth(world.clinicians[0]))
    assert response.status_code == 405
    assert response.json() == {"error": "method_not_allowed"}


@pytest.mark.parametrize(
    "path", ["/v1/export", "/v1/patients/export", "/fhir/Patient", "/docs", "/v1/bulk"]
)
def test_unknown_endpoints_do_not_exist(
    client: TestClient, world: World, auth: Auth, path: str
) -> None:
    for method in ("GET", "POST"):
        response = client.request(method, path, headers=auth(world.clinicians[0]))
        assert response.status_code in (404, 405)


# Failure modes ----------------------------------------------------------------------


def test_fhir_outage_denies_and_is_audited(
    client: TestClient, world: World, fhir: InMemoryFhir, auth: Auth, settings: Settings
) -> None:
    fhir.unavailable = True
    response = client.get("/v1/patients", headers=auth(world.clinicians[0]))
    assert response.status_code == 503
    assert response.json() == {"error": "upstream_unavailable"}
    last = _records(settings)[-1]
    assert last["decision"] == "deny"
    assert last["reason"] == "upstream_unavailable"


def test_tampered_audit_log_prevents_startup(
    settings: Settings, fhir: InMemoryFhir, client: TestClient, world: World, auth: Auth
) -> None:
    client.get("/v1/patients", headers=auth(world.clinicians[0]))
    lines = settings.audit_path.read_text().splitlines()
    record = json.loads(lines[0])
    record["decision"] = "allow" if record["decision"] == "deny" else "deny"
    settings.audit_path.write_text(json.dumps(record) + "\n" + "\n".join(lines[1:]))
    with pytest.raises(AuditIntegrityError):
        create_app(settings, transport=fhir.transport())


def test_unwritable_audit_log_withholds_the_response(
    client: TestClient, world: World, auth: Auth, settings: Settings
) -> None:
    settings.audit_path.unlink(missing_ok=True)
    settings.audit_path.mkdir()
    response = client.get("/v1/patients", headers=auth(world.clinicians[0]))
    assert response.status_code == 503
    assert "patient" not in response.text


# Audit coverage ---------------------------------------------------------------------


def test_every_request_produces_exactly_one_audit_record(
    make_client: MakeClient, world: World, auth: Auth, alias: Alias, settings: Settings
) -> None:
    client = make_client()
    clinician = world.clinicians[0]
    inside = sorted(world.panels[clinician])[0]
    outside = sorted(set(world.patients) - world.panels[clinician])[0]
    calls = [
        ("GET", "/healthz", {}),
        ("GET", "/v1/patients", {}),
        ("GET", "/v1/patients", auth(clinician)),
        ("GET", f"/v1/patients/{alias(clinician, Kind.PATIENT, inside)}/reports", auth(clinician)),
        ("GET", f"/v1/patients/{alias(clinician, Kind.PATIENT, outside)}/reports", auth(clinician)),
        ("GET", f"/v1/patients/{outside}/reports", auth(clinician)),
        ("DELETE", "/v1/patients", auth(clinician)),
        ("GET", "/v1/nothing-here", auth(clinician)),
        ("POST", "/v1/actions/follow-up/prepare", auth(clinician)),
    ]
    for method, path, headers in calls:
        client.request(method, path, headers=headers)

    records = _records(settings)
    assert len(records) == len(calls)
    assert verify(settings.audit_path).ok
    decisions = [(r["decision"], r["reason"]) for r in records]
    assert decisions == [
        ("allow", "ok"),
        ("deny", "invalid_token"),
        ("allow", "ok"),
        ("allow", "ok"),
        ("deny", "not_found"),
        ("deny", "not_found"),
        ("deny", "method_not_allowed"),
        ("deny", "not_found"),
        ("deny", "bad_request"),
    ]
    assert records[4]["patient"] is None
    assert records[5]["resource"] == outside
    assert records[3]["patient"] == inside
    assert all(r["clinician"] == clinician for r in records[2:6])


def test_audit_records_contain_no_record_content(
    client: TestClient, world: World, auth: Auth, alias: Alias, settings: Settings
) -> None:
    clinician = world.clinicians[0]
    for patient in sorted(world.panels[clinician]):
        p_ref = alias(clinician, Kind.PATIENT, patient)
        for report in world.reports.get(patient, []):
            client.get(
                f"/v1/patients/{p_ref}/reports/{alias(clinician, Kind.REPORT, report)}",
                headers=auth(clinician),
            )
    text = Path(settings.audit_path).read_text()
    for value in _sensitive_values(world):
        assert value not in text
    for resource in world.resources("DiagnosticReport"):
        assert resource["conclusion"][:40] not in text
