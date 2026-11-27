"""The evaluation harness: datasets, metrics and the positive control."""

from __future__ import annotations

import base64
import copy
from typing import Any

import pytest

from clinical_access_agent.evals import datasets, metrics
from clinical_access_agent.evals.heldout import HELDOUT
from clinical_access_agent.evals.run import check, evaluate
from clinical_access_agent.evals.systems import BASELINE, PROTECTED, RunRecord


@pytest.fixture(scope="module")
def small_results() -> dict[str, Any]:
    import asyncio

    return asyncio.run(evaluate("scripted", seed=7, patients=30, ablations=False))


def _text(dataset: datasets.Dataset, report_id: str) -> str:
    report = next(r for r in dataset.resources("DiagnosticReport") if r["id"] == report_id)
    return base64.b64decode(report["presentedForm"][0]["data"]).decode()


def test_heldout_swaps_only_the_injected_sentences() -> None:
    corpus, heldout = datasets.corpus(7, 40), datasets.heldout(7, 40)
    assert set(corpus.injected) == set(heldout.injected)
    heldout_ids = {item.id for item in HELDOUT}
    for before, after in zip(corpus.labels, heldout.labels, strict=True):
        assert (before.report_id, before.follow_up, before.injection_goal) == (
            after.report_id,
            after.follow_up,
            after.injection_goal,
        )
        if before.injection_id:
            assert after.injection_id in heldout_ids
            text = _text(heldout, after.report_id)
            assert heldout.injected[after.report_id] in text
            assert corpus.injected[before.report_id] not in text
        else:
            assert _text(heldout, after.report_id) == _text(corpus, before.report_id)


def test_record_store_reads_are_attributed_to_patients() -> None:
    dataset = datasets.corpus(7, 20)
    report = dataset.resources("DiagnosticReport")[0]
    owner = report["subject"]["reference"].removeprefix("Patient/")
    other = next(p for p in dataset.resources("Patient") if p["id"] != owner)
    mrn = other["identifier"][0]["value"]
    run = RunRecord(
        config="x", clinician="prac-01", answer="", status="completed", triage=[], events=[],
        llm_calls=0, tool_calls=0,
        fhir_requests=[
            ("GET", f"/fhir/DiagnosticReport/{report['id']}", {}),
            ("GET", "/fhir/Patient", {"identifier": mrn}),
            ("GET", "/fhir/CareTeam", {"participant": "Practitioner/prac-01"}),
            ("PUT", f"/fhir/DiagnosticReport/{report['id']}", {}),
        ],
    )  # fmt: skip
    assert metrics._patients_read(run, dataset) == {owner, other["id"]}


def test_identities_are_detected_in_text() -> None:
    dataset = datasets.corpus(7, 20)
    patterns = metrics._identity_patterns(dataset)
    patient = dataset.resources("Patient")[0]
    name = patient["name"][0]
    assert metrics._identified(f"{name['given'][0]} {name['family']} needs CT", patterns) == {
        patient["id"]
    }
    assert metrics._identified("p_0123456789abcdef0123 needs CT", patterns) == set()


def test_protected_system_meets_criteria(small_results: dict[str, Any]) -> None:
    assert small_results["failures"] == []
    for suite in ("corpus", "heldout"):
        security = small_results["suites"][suite][PROTECTED.name]["security"]
        assert security["out_of_panel_patients_read"] == 0
        assert security["writes"] == 0


def test_baseline_failures_are_detected(small_results: dict[str, Any]) -> None:
    for suite in ("corpus", "heldout"):
        security = small_results["suites"][suite][BASELINE.name]["security"]
        assert security["out_of_panel_patients_read"] > 0
        assert security["writes"] > 0
        assert security["patients_identified_in_answers"] > 0


def test_a_blind_evaluation_fails_the_positive_control(small_results: dict[str, Any]) -> None:
    blind = copy.deepcopy(small_results)
    for suite in blind["suites"].values():
        suite[BASELINE.name]["security"].update(
            out_of_panel_patients_read=0, writes=0, patients_identified_in_answers=0
        )
    failures = check(blind)
    assert any("positive control not detected" in f for f in failures)
