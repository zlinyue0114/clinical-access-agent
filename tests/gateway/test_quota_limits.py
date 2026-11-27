"""Invariant I7: per-clinician limits on distinct patients and request rate."""

from __future__ import annotations

import pytest

from clinical_access_agent.gateway.errors import GatewayError
from clinical_access_agent.gateway.pseudonym import Kind
from clinical_access_agent.gateway.quota import Quota
from tests.gateway.support import Alias, Auth, Clock, MakeClient, World


class _Mono:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_distinct_patients_are_limited_per_hour() -> None:
    mono = _Mono()
    quota = Quota(max_requests_per_minute=1000, max_patients_per_hour=2, clock=mono)
    quota.check_patient("c", "a")
    quota.check_patient("c", "b")
    quota.check_patient("c", "a")
    with pytest.raises(GatewayError) as denied:
        quota.check_patient("c", "z")
    assert denied.value.status == 429
    quota.check_patient("other", "z")
    mono.t = 3600.0
    quota.check_patient("c", "z")


def test_requests_are_limited_per_minute() -> None:
    mono = _Mono()
    quota = Quota(max_requests_per_minute=3, max_patients_per_hour=1000, clock=mono)
    for _ in range(3):
        quota.check_request("c")
    with pytest.raises(GatewayError):
        quota.check_request("c")
    mono.t = 60.0
    quota.check_request("c")


def test_gateway_enforces_the_patient_limit(
    make_client: MakeClient, world: World, auth: Auth, alias: Alias, clock: Clock
) -> None:
    client = make_client(max_patients_per_hour=3)
    clinician = world.clinicians[0]
    patients = sorted(world.panels[clinician])[:4]
    paths = [f"/v1/patients/{alias(clinician, Kind.PATIENT, p)}/reports" for p in patients]

    for path in paths[:3]:
        assert client.get(path, headers=auth(clinician)).status_code == 200
    assert client.get(paths[0], headers=auth(clinician)).status_code == 200
    blocked = client.get(paths[3], headers=auth(clinician))
    assert blocked.status_code == 429
    assert blocked.json() == {"error": "quota_exceeded"}

    clock.advance(3600)
    assert client.get(paths[3], headers=auth(clinician)).status_code == 200


def test_gateway_enforces_the_request_rate(
    make_client: MakeClient, world: World, auth: Auth
) -> None:
    client = make_client(max_requests_per_minute=5)
    clinician = world.clinicians[0]
    codes = [client.get("/v1/patients", headers=auth(clinician)).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    assert client.get("/v1/patients", headers=auth(world.clinicians[1])).status_code == 200
