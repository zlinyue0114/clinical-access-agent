import base64
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from clinical_access_agent.data.generate import Label, generate, write
from clinical_access_agent.data.injections import CORPUS, Goal
from clinical_access_agent.data.rules import FollowUp

_CJK_RANGES = [
    (0x3000, 0x303F),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
]
CJK = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _CJK_RANGES) + "]")


@pytest.fixture(scope="module")
def dataset() -> tuple[dict[str, Any], list[Label]]:
    return generate(seed=7)


def _resources(bundle: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == kind]


def test_generation_is_deterministic(tmp_path: Path) -> None:
    first = write(tmp_path / "a", seed=7, patient_count=30, injection_rate=0.20)
    second = write(tmp_path / "b", seed=7, patient_count=30, injection_rate=0.20)
    other = write(tmp_path / "c", seed=8, patient_count=30, injection_rate=0.20)
    assert first["sha256"] == second["sha256"]
    assert first["sha256"] != other["sha256"]


def test_counts(dataset: tuple[dict[str, Any], list[Label]]) -> None:
    bundle, labels = dataset
    assert len(_resources(bundle, "Practitioner")) == 6
    assert len(_resources(bundle, "Patient")) == 120
    reports = _resources(bundle, "DiagnosticReport")
    assert len(reports) == len(labels) == len(_resources(bundle, "ImagingStudy"))
    assert 150 <= len(reports) <= 280


def test_ids_unique_and_references_resolve(dataset: tuple[dict[str, Any], list[Label]]) -> None:
    bundle, _ = dataset
    keys = [f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in bundle["entry"]]
    assert len(keys) == len(set(keys))
    known = set(keys)
    refs = re.findall(r'"reference": "([A-Za-z]+/[^"]+)"', json.dumps(bundle))
    assert refs
    assert set(refs) <= known


def test_every_patient_has_a_care_team(dataset: tuple[dict[str, Any], list[Label]]) -> None:
    bundle, _ = dataset
    covered = {ct["subject"]["reference"] for ct in _resources(bundle, "CareTeam")}
    patients = {f"Patient/{p['id']}" for p in _resources(bundle, "Patient")}
    assert covered == patients


def test_labels_match_reports(dataset: tuple[dict[str, Any], list[Label]]) -> None:
    bundle, labels = dataset
    report_ids = {r["id"] for r in _resources(bundle, "DiagnosticReport")}
    assert {label.report_id for label in labels} == report_ids


def test_ground_truth_not_leaked_into_bundle(
    dataset: tuple[dict[str, Any], list[Label]],
) -> None:
    bundle, _ = dataset
    text = json.dumps(bundle)
    for marker in ("follow_up", "injection_", "nodule_mm", "risk"):
        assert marker not in text


def test_all_follow_up_classes_present(dataset: tuple[dict[str, Any], list[Label]]) -> None:
    _, labels = dataset
    assert {label.follow_up for label in labels} == set(FollowUp)


def test_injection_rate_and_coverage(dataset: tuple[dict[str, Any], list[Label]]) -> None:
    _, labels = dataset
    injected = [label for label in labels if label.injection_id]
    assert 0.15 <= len(injected) / len(labels) <= 0.25
    assert {label.injection_language for label in injected} == {"en", "de"}
    used = Counter(label.injection_id for label in injected)
    assert set(used) == {item.id for item in CORPUS}
    assert max(used.values()) - min(used.values()) <= 2


def test_tampering_only_where_follow_up_required(
    dataset: tuple[dict[str, Any], list[Label]],
) -> None:
    _, labels = dataset
    for label in labels:
        if label.injection_goal == Goal.CLINICAL_TAMPERING:
            assert label.follow_up is FollowUp.REQUIRED


def test_injection_target_is_outside_the_care_teams(
    dataset: tuple[dict[str, Any], list[Label]],
) -> None:
    bundle, labels = dataset
    teams: dict[str, set[str]] = {}
    for ct in _resources(bundle, "CareTeam"):
        patient = ct["subject"]["reference"].removeprefix("Patient/")
        team = ct["identifier"][0]["value"]
        teams.setdefault(patient, set()).add(team)
    for label in labels:
        if label.injection_target_patient:
            assert not teams[label.patient_id] & teams[label.injection_target_patient]


def test_injection_text_present_in_report(dataset: tuple[dict[str, Any], list[Label]]) -> None:
    bundle, labels = dataset
    texts = {
        r["id"]: base64.b64decode(r["presentedForm"][0]["data"]).decode("utf-8")
        for r in _resources(bundle, "DiagnosticReport")
    }
    corpus = {item.id: item for item in CORPUS}
    for label in labels:
        if label.injection_id:
            expected = corpus[label.injection_id].text.format(other=label.injection_target_patient)
            assert expected in texts[label.report_id]


def test_no_cjk_characters_anywhere(dataset: tuple[dict[str, Any], list[Label]]) -> None:
    bundle, labels = dataset
    decoded = [
        base64.b64decode(r["presentedForm"][0]["data"]).decode("utf-8")
        for r in _resources(bundle, "DiagnosticReport")
    ]
    blob = json.dumps(bundle, ensure_ascii=False) + "".join(decoded)
    blob += "".join(json.dumps(label.__dict__, default=str) for label in labels)
    assert not CJK.search(blob)
