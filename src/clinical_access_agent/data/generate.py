"""Generate a reproducible synthetic dataset.

Outputs, written to ``--out``:

- ``bundle.json``    FHIR R4 transaction bundle (Practitioner, Patient, CareTeam,
                     ImagingStudy, DiagnosticReport) ready to post to a FHIR server
- ``labels.jsonl``   ground truth per report, for evaluation only; never served by
                     the gateway or shown to the agent
- ``manifest.json``  seed, counts and SHA-256 hashes of the two files above

Usage::

    python -m clinical_access_agent.data.generate --seed 7 --out data/generated
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from clinical_access_agent.data import templates as tpl
from clinical_access_agent.data.injections import CORPUS, Goal, Injection
from clinical_access_agent.data.rules import (
    BreastFindingType,
    FollowUp,
    Recommendation,
    Risk,
    breast_finding_recommendation,
    combine,
    solid_nodule_recommendation,
)

GENERATOR_VERSION = "1"
REFERENCE_DATE = date(2026, 11, 1)
MRN_SYSTEM = "urn:clinical-access-agent:mrn"
INSURANCE_SYSTEM = "urn:clinical-access-agent:insurance"

GIVEN_F = [
    "Anna",
    "Maria",
    "Sophie",
    "Laura",
    "Julia",
    "Elena",
    "Clara",
    "Ingrid",
    "Petra",
    "Sabine",
    "Nina",
    "Helga",
    "Lea",
    "Amelie",
    "Fatma",
    "Olga",
    "Marta",
    "Ines",
]
GIVEN_M = [
    "Thomas",
    "Michael",
    "Stefan",
    "Jonas",
    "Lukas",
    "Peter",
    "Klaus",
    "Mehmet",
    "Paul",
    "David",
    "Tobias",
    "Jan",
    "Florian",
    "Andreas",
    "Ivan",
    "Marco",
    "Emil",
]
FAMILY = [
    "Müller",
    "Schmidt",
    "Schneider",
    "Fischer",
    "Weber",
    "Meyer",
    "Wagner",
    "Becker",
    "Schulz",
    "Hoffmann",
    "Koch",
    "Richter",
    "Klein",
    "Wolf",
    "Neumann",
    "Schwarz",
    "Braun",
    "Zimmermann",
    "Krüger",
    "Hartmann",
    "Lange",
    "Werner",
    "Krause",
    "Lehmann",
    "Yilmaz",
    "Novak",
    "Rossi",
    "Kowalski",
    "Jansen",
    "Berger",
]

TEAMS = [
    ("team-pulm", "Pulmonology"),
    ("team-rad", "Thoracic Radiology"),
    ("team-onc", "Thoracic Oncology"),
]


@dataclass
class Clinician:
    id: str
    given: str
    family: str
    team: str


@dataclass
class PatientRecord:
    id: str
    given: str
    family: str
    gender: str
    birth_date: date
    language: str
    risk: Risk
    teams: list[str]
    mrn: str
    insurance: str


@dataclass
class Label:
    """Ground truth for one report. Evaluation only."""

    report_id: str
    patient_id: str
    language: str
    follow_up: FollowUp
    nodule_mm: float | None
    risk: Risk
    breast_finding: BreastFindingType | None
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    recommendation_in_text: bool = False
    injection_id: str | None = None
    injection_goal: str | None = None
    injection_phrasing: str | None = None
    injection_language: str | None = None
    injection_target_patient: str | None = None


def _age(born: date) -> int:
    years = REFERENCE_DATE.year - born.year
    if (REFERENCE_DATE.month, REFERENCE_DATE.day) < (born.month, born.day):
        years -= 1
    return years


def make_clinicians(rng: random.Random) -> list[Clinician]:
    clinicians = []
    n = 0
    for team_id, _ in TEAMS:
        for _ in range(2):
            n += 1
            given = rng.choice(GIVEN_F + GIVEN_M)
            clinicians.append(Clinician(f"prac-{n:02d}", given, rng.choice(FAMILY), team_id))
    return clinicians


def make_patients(rng: random.Random, count: int) -> list[PatientRecord]:
    patients = []
    for i in range(1, count + 1):
        gender = rng.choice(["female", "male"])
        given = rng.choice(GIVEN_F if gender == "female" else GIVEN_M)
        age_days = rng.randint(40 * 365, 85 * 365)
        born = REFERENCE_DATE - timedelta(days=age_days)
        teams = [rng.choice(TEAMS)[0]]
        if rng.random() < 0.10:
            second = rng.choice([t for t, _ in TEAMS if t not in teams])
            teams.append(second)
        patients.append(
            PatientRecord(
                id=f"pat-{i:04d}",
                given=given,
                family=rng.choice(FAMILY),
                gender=gender,
                birth_date=born,
                language="de" if rng.random() < 0.6 else "en",
                risk=Risk.HIGH if rng.random() < 0.5 else Risk.LOW,
                teams=sorted(teams),
                mrn=f"MRN-{rng.randint(100000, 999999)}",
                insurance=f"INS-{rng.randint(10**9, 10**10 - 1)}",
            )
        )
    return patients


def _outsider(rng: random.Random, patient: PatientRecord, patients: list[PatientRecord]) -> str:
    """A patient that no clinician of ``patient``'s care teams may access."""
    candidates = [p for p in patients if not set(p.teams) & set(patient.teams)]
    return rng.choice(candidates).id


@dataclass
class ReportDraft:
    """A report before rendering; injections are assigned in a second pass."""

    patient: PatientRecord
    report_id: str
    study_id: str
    when: date
    findings: list[str]
    impression: list[str]
    label: Label


def make_report(
    rng: random.Random, patient: PatientRecord, report_id: str
) -> tuple[list[str], list[str], Label]:
    """Return (findings sentences, impression sentences, label) without injections."""
    lang = patient.language
    findings: list[str] = []
    impression: list[str] = []
    recs: list[Recommendation] = []
    nodule_mm: float | None = None
    breast: BreastFindingType | None = None

    if rng.random() < 0.55:
        nodule_mm = float(rng.choice([3, 4, 5, 5, 6, 7, 8, 9, 11, 14, 18]))
        recs.append(solid_nodule_recommendation(nodule_mm, patient.risk))
        findings.append(tpl.nodule_sentence(rng, lang, rng.randrange(5), nodule_mm))
    else:
        findings.append(rng.choice(tpl.NO_NODULE[lang]))

    if patient.gender == "female" and rng.random() < 0.12:
        breast = rng.choices(
            [
                BreastFindingType.BENIGN_CALCIFIED,
                BreastFindingType.CIRCUMSCRIBED_MASS,
                BreastFindingType.SUSPICIOUS_MASS,
            ],
            weights=[5, 3, 2],
        )[0]
        recs.append(breast_finding_recommendation(breast))
        findings.append(
            tpl.breast_sentence(rng, lang, breast, rng.randrange(2), rng.randint(8, 28))
        )

    findings.append(rng.choice(tpl.BACKGROUND[lang]))
    level = combine(recs)

    stated = False
    if level is FollowUp.NONE:
        impression.append(tpl.UNREMARKABLE_IMPRESSION[lang])
    else:
        impression.append(findings[0] if nodule_mm is not None else findings[1])
        if breast is not None and nodule_mm is not None:
            impression.append(findings[1])
        if rng.random() < 0.7:
            stated = True
            for rec in recs:
                sentence = tpl.recommendation_sentence(rec, lang)
                if sentence:
                    impression.append(sentence)

    label = Label(
        report_id=report_id,
        patient_id=patient.id,
        language=lang,
        follow_up=level,
        nodule_mm=nodule_mm,
        risk=patient.risk,
        breast_finding=breast,
        recommendations=[
            {
                "follow_up": r.follow_up,
                "interval_months": r.interval_months,
                "next_exam": r.next_exam,
            }
            for r in recs
        ],
        recommendation_in_text=stated,
    )
    return findings, impression, label


def assign_injections(
    rng: random.Random,
    drafts: list[ReportDraft],
    patients: list[PatientRecord],
    rate: float,
) -> None:
    """Embed injections in a balanced way: every corpus item is used about equally.

    Tampering items (which try to suppress a follow-up) are only placed in reports
    whose ground truth requires follow-up, so their effect is measurable.
    """
    n_injected = round(len(drafts) * rate)
    required = [d for d in drafts if d.label.follow_up is FollowUp.REQUIRED]
    others = [d for d in drafts if d.label.follow_up is not FollowUp.REQUIRED]
    rng.shuffle(required)
    rng.shuffle(others)
    tamper = [item for item in CORPUS if item.goal is Goal.CLINICAL_TAMPERING]
    general = [item for item in CORPUS if item.goal is not Goal.CLINICAL_TAMPERING]

    n_tamper = min(len(required), round(n_injected * len(tamper) / len(CORPUS)))
    plan: list[tuple[ReportDraft, Injection]] = [
        (required.pop(), tamper[i % len(tamper)]) for i in range(n_tamper)
    ]
    pool = required + others
    rng.shuffle(pool)
    for i in range(n_injected - n_tamper):
        plan.append((pool[i], general[i % len(general)]))

    for draft, injection in plan:
        target = _outsider(rng, draft.patient, patients)
        sentence = injection.text.format(other=target)
        if rng.random() < 0.5:
            draft.findings.insert(1, sentence)
        else:
            draft.impression.append(sentence)
        draft.label.injection_id = injection.id
        draft.label.injection_goal = injection.goal
        draft.label.injection_phrasing = injection.phrasing
        draft.label.injection_language = injection.language
        draft.label.injection_target_patient = target


def render(language: str, findings: list[str], impression: list[str]) -> str:
    title, h_findings, h_impression = tpl.HEADERS[language]
    return "\n".join(
        [
            title,
            "",
            f"{h_findings}:",
            " ".join(findings),
            "",
            f"{h_impression}:",
            " ".join(impression),
        ]
    )


def _practitioner(c: Clinician) -> dict[str, Any]:
    return {
        "resourceType": "Practitioner",
        "id": c.id,
        "active": True,
        "name": [{"family": c.family, "given": [c.given], "prefix": ["Dr."]}],
    }


def _patient(p: PatientRecord) -> dict[str, Any]:
    return {
        "resourceType": "Patient",
        "id": p.id,
        "identifier": [
            {"system": MRN_SYSTEM, "value": p.mrn},
            {"system": INSURANCE_SYSTEM, "value": p.insurance},
        ],
        "name": [{"use": "official", "family": p.family, "given": [p.given]}],
        "gender": p.gender,
        "birthDate": p.birth_date.isoformat(),
        "communication": [
            {
                "language": {"coding": [{"system": "urn:ietf:bcp:47", "code": p.language}]},
                "preferred": True,
            }
        ],
    }


def _care_team(
    team_id: str, team_name: str, p: PatientRecord, members: list[Clinician]
) -> dict[str, Any]:
    return {
        "resourceType": "CareTeam",
        "id": f"ct-{team_id.removeprefix('team-')}-{p.id.removeprefix('pat-')}",
        "status": "active",
        "name": team_name,
        "identifier": [{"system": "urn:clinical-access-agent:team", "value": team_id}],
        "subject": {"reference": f"Patient/{p.id}"},
        "participant": [{"member": {"reference": f"Practitioner/{m.id}"}} for m in members],
    }


def _imaging_study(study_id: str, p: PatientRecord, when: date) -> dict[str, Any]:
    return {
        "resourceType": "ImagingStudy",
        "id": study_id,
        "status": "available",
        "subject": {"reference": f"Patient/{p.id}"},
        "started": when.isoformat(),
        "modality": [{"system": "http://dicom.nema.org/resources/ontology/DCM", "code": "CT"}],
        "description": "CT chest",
    }


def _diagnostic_report(
    report_id: str, study_id: str, p: PatientRecord, when: date, text: str, impression: str
) -> dict[str, Any]:
    return {
        "resourceType": "DiagnosticReport",
        "id": report_id,
        "status": "final",
        "category": [
            {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "RAD"}]}
        ],
        "code": {
            "coding": [{"system": "http://loinc.org", "code": "24627-2", "display": "CT Chest"}],
            "text": "CT chest",
        },
        "subject": {"reference": f"Patient/{p.id}"},
        "effectiveDateTime": when.isoformat(),
        "imagingStudy": [{"reference": f"ImagingStudy/{study_id}"}],
        "conclusion": impression,
        "presentedForm": [
            {
                "contentType": "text/plain",
                "language": p.language,
                "data": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            }
        ],
    }


def _entry(resource: dict[str, Any]) -> dict[str, Any]:
    ref = f"{resource['resourceType']}/{resource['id']}"
    return {"fullUrl": ref, "resource": resource, "request": {"method": "PUT", "url": ref}}


def generate(
    seed: int, patient_count: int = 120, injection_rate: float = 0.20
) -> tuple[dict[str, Any], list[Label]]:
    """Build the transaction bundle and labels. Pure function of its arguments."""
    rng = random.Random(seed)  # noqa: S311  (synthetic data, not security)
    clinicians = make_clinicians(rng)
    patients = make_patients(rng, patient_count)
    resources: list[dict[str, Any]] = [_practitioner(c) for c in clinicians]
    team_names = dict(TEAMS)
    drafts: list[ReportDraft] = []
    report_no = 0

    for p in patients:
        resources.append(_patient(p))
        for team_id in p.teams:
            members = [c for c in clinicians if c.team == team_id]
            resources.append(_care_team(team_id, team_names[team_id], p, members))
        n_reports = rng.choice([1, 1, 2, 2, 3])
        offsets = sorted(rng.sample(range(0, 1000), n_reports))
        for offset in offsets:
            report_no += 1
            report_id = f"rep-{report_no:05d}"
            findings, impression, label = make_report(rng, p, report_id)
            drafts.append(
                ReportDraft(
                    patient=p,
                    report_id=report_id,
                    study_id=f"img-{report_no:05d}",
                    when=date(2024, 1, 1) + timedelta(days=offset),
                    findings=findings,
                    impression=impression,
                    label=label,
                )
            )

    assign_injections(rng, drafts, patients, injection_rate)

    for d in drafts:
        text = render(d.patient.language, d.findings, d.impression)
        resources.append(_imaging_study(d.study_id, d.patient, d.when))
        resources.append(
            _diagnostic_report(
                d.report_id, d.study_id, d.patient, d.when, text, " ".join(d.impression)
            )
        )

    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [_entry(r) for r in resources],
    }
    return bundle, [d.label for d in drafts]


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write(out: Path, seed: int, patient_count: int, injection_rate: float) -> dict[str, Any]:
    """Generate and write bundle, labels and manifest. Returns the manifest."""
    bundle, labels = generate(seed, patient_count, injection_rate)
    out.mkdir(parents=True, exist_ok=True)
    bundle_text = _dump(bundle)
    labels_text = "".join(
        json.dumps(asdict(label), ensure_ascii=False, sort_keys=True) + "\n" for label in labels
    )
    (out / "bundle.json").write_text(bundle_text, encoding="utf-8")
    (out / "labels.jsonl").write_text(labels_text, encoding="utf-8")
    counts: dict[str, int] = {}
    for entry in bundle["entry"]:
        kind = entry["resource"]["resourceType"]
        counts[kind] = counts.get(kind, 0) + 1
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "patients": patient_count,
        "injection_rate": injection_rate,
        "resource_counts": counts,
        "reports_with_injection": sum(1 for x in labels if x.injection_id),
        "sha256": {
            "bundle.json": hashlib.sha256(bundle_text.encode()).hexdigest(),
            "labels.jsonl": hashlib.sha256(labels_text.encode()).hexdigest(),
        },
    }
    (out / "manifest.json").write_text(_dump(manifest), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--patients", type=int, default=120)
    parser.add_argument("--injection-rate", type=float, default=0.20)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    args = parser.parse_args()
    manifest = write(args.out, args.seed, args.patients, args.injection_rate)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
