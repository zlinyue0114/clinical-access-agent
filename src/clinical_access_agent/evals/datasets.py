"""Evaluation datasets.

``corpus``   the generated dataset as is; injections come from the generation corpus.
``heldout``  the same records and labels, with every injected sentence replaced by
             a held-out item of the same goal (see ``heldout.py``). Cross-patient
             items name the same target patient, by record number.
"""

from __future__ import annotations

import base64
import copy
import itertools
from dataclasses import dataclass, replace
from typing import Any

from clinical_access_agent.data.generate import Label, generate
from clinical_access_agent.data.injections import CORPUS, Goal, Injection
from clinical_access_agent.evals.heldout import HELDOUT

DEFAULT_SEED = 7
DEFAULT_PATIENTS = 120


@dataclass(frozen=True)
class Dataset:
    name: str
    bundle: dict[str, Any]
    labels: list[Label]
    # report id -> injected sentence exactly as it appears in the report text
    injected: dict[str, str]

    def resources(self, kind: str) -> list[dict[str, Any]]:
        return [
            e["resource"] for e in self.bundle["entry"] if e["resource"]["resourceType"] == kind
        ]

    def panel(self, clinician: str) -> set[str]:
        member = f"Practitioner/{clinician}"
        return {
            team["subject"]["reference"].removeprefix("Patient/")
            for team in self.resources("CareTeam")
            if team.get("status") == "active"
            and any(p["member"]["reference"] == member for p in team["participant"])
        }

    def clinicians(self) -> list[str]:
        return sorted(r["id"] for r in self.resources("Practitioner"))


def _corpus_sentence(label: Label) -> str:
    item = next(i for i in CORPUS if i.id == label.injection_id)
    return item.text.format(other=label.injection_target_patient)


def corpus(seed: int = DEFAULT_SEED, patients: int = DEFAULT_PATIENTS) -> Dataset:
    bundle, labels = generate(seed, patients)
    injected = {label.report_id: _corpus_sentence(label) for label in labels if label.injection_id}
    return Dataset("corpus", bundle, labels, injected)


def heldout(seed: int = DEFAULT_SEED, patients: int = DEFAULT_PATIENTS) -> Dataset:
    base = corpus(seed, patients)
    bundle = copy.deepcopy(base.bundle)
    mrn = {
        p["id"]: next(i["value"] for i in p["identifier"] if "mrn" in i["system"])
        for p in base.resources("Patient")
    }
    by_goal: dict[str, itertools.cycle[Injection]] = {
        goal.value: itertools.cycle([i for i in HELDOUT if i.goal is goal]) for goal in Goal
    }
    reports = {
        e["resource"]["id"]: e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "DiagnosticReport"
    }

    labels: list[Label] = []
    injected: dict[str, str] = {}
    for label in base.labels:
        if not label.injection_id:
            labels.append(label)
            continue
        old = base.injected[label.report_id]
        item = next(by_goal[str(label.injection_goal)])
        new = item.text.format(other=mrn[str(label.injection_target_patient)])
        report = reports[label.report_id]
        form = report["presentedForm"][0]
        text = base64.b64decode(form["data"]).decode()
        assert old in text, label.report_id
        form["data"] = base64.b64encode(text.replace(old, new).encode()).decode()
        report["conclusion"] = report["conclusion"].replace(old, new)
        injected[label.report_id] = new
        labels.append(
            replace(
                label,
                injection_id=item.id,
                injection_phrasing=item.phrasing,
                injection_language=item.language,
            )
        )
    return Dataset("heldout", bundle, labels, injected)
