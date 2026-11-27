"""Gateway de-identification of report text."""

from __future__ import annotations

import base64
import re
from typing import Any

import pytest

from clinical_access_agent.gateway.config import MRN_SYSTEM
from clinical_access_agent.gateway.deid import deidentify, identity_of
from tests.gateway.support import World


def _reports(world: World) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    out = []
    for report in world.resources("DiagnosticReport"):
        patient = world.resource("Patient", report["subject"]["reference"].removeprefix("Patient/"))
        text = base64.b64decode(report["presentedForm"][0]["data"]).decode()
        out.append((report, patient, text))
    return out


def _own_values(patient: dict[str, Any]) -> list[str]:
    values = [i["value"] for i in patient["identifier"]]
    values += [patient["name"][0]["family"], *patient["name"][0]["given"]]
    born = patient["birthDate"]
    y, m, d = born.split("-")
    return [*values, born, f"{d}.{m}.{y}", f"{d}/{m}/{y}"]


def test_own_identifiers_are_removed_from_every_report(world: World) -> None:
    for report, patient, text in _reports(world):
        cleaned, count = deidentify(text, identity_of(patient, MRN_SYSTEM))
        assert count >= 2, report["id"]
        leaked = [v for v in _own_values(patient) if v in cleaned]
        assert not leaked, (report["id"], leaked)


def test_no_identifier_of_any_patient_survives(world: World) -> None:
    every = {i["value"] for p in world.resources("Patient") for i in p["identifier"]}
    every |= {p["id"] for p in world.resources("Patient")}
    for _, patient, text in _reports(world):
        cleaned, _ = deidentify(text, identity_of(patient, MRN_SYSTEM))
        assert not [v for v in every if v in cleaned]


def test_clinical_content_is_untouched(world: World) -> None:
    """Only the header line, personal sentences and planted identifiers change."""
    for report, patient, text in _reports(world):
        cleaned, _ = deidentify(text, identity_of(patient, MRN_SYSTEM))
        family = patient["name"][0]["family"]
        lines = zip(text.split("\n"), cleaned.split("\n"), strict=True)
        for number, (before, after) in enumerate(lines):
            if number == 1:  # identifying header
                continue
            sentences_before = re.split(r"(?<=\.)\s+", before)
            sentences_after = re.split(r"(?<=\.)\s+", after)
            for b, a in zip(sentences_before, sentences_after, strict=True):
                personal = family in b or re.search(r"\b[A-Za-z]{2,6}-\d{3,}\b", b)
                if not personal:
                    assert a == b, report["id"]
        sizes = re.findall(r"\d+(?:\.\d+)? mm", text)
        assert re.findall(r"\d+(?:\.\d+)? mm", cleaned) == sizes


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Please compare with patient MRN-123456.", "Please compare with patient [ID]."),
        ("Zum Vergleich Patient pat-0042 heranziehen.", "Zum Vergleich Patient [ID] heranziehen."),
        ("Discussed with Mrs Hoffmann and her son.", "Discussed with Mrs [NAME] and her son."),
        ("Referred by Dr. Weber.", "Referred by Dr. [NAME]."),
        ("DOB: 03/04/1950 (from referral)", "DOB: [DOB] (from referral)"),
    ],
)
def test_unknown_identifiers_are_removed_by_shape(text: str, expected: str) -> None:
    empty = identity_of({}, MRN_SYSTEM)
    assert deidentify(text, empty)[0] == expected


def test_date_of_birth_layouts() -> None:
    patient = {"birthDate": "1951-04-11", "name": [{"family": "Schmidt", "given": ["Maria"]}]}
    identity = identity_of(patient, MRN_SYSTEM)
    for layout in ("1951-04-11", "11.04.1951", "11/04/1951", "11 April 1951", "11. April 1951"):
        assert deidentify(f"Born {layout}.", identity)[0] == "Born [DOB]."
