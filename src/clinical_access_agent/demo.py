"""Offline walkthrough: ``make demo`` (or ``caa-demo``).

Runs the whole stack in one process on generated data with the scripted model, no
Docker and no cloud access, and prints four short sections:

1. a clinician's question and the agent's answer;
2. a report with injected text, as the agent received it;
3. the same attack with every text and agent-side layer off, stopped by the gateway;
4. the audit log, verified.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from clinical_access_agent.agent.agent import AgentResult, ask
from clinical_access_agent.agent.client import GatewayClient
from clinical_access_agent.agent.guard import SessionGuard
from clinical_access_agent.agent.screening import screen
from clinical_access_agent.agent.stub import ScriptedModel
from clinical_access_agent.data.generate import generate
from clinical_access_agent.data.injections import Goal
from clinical_access_agent.data.memory_fhir import InMemoryFhir
from clinical_access_agent.gateway import audit
from clinical_access_agent.gateway.app import create_app
from clinical_access_agent.gateway.config import Settings
from clinical_access_agent.gateway.pseudonym import Kind, Pseudonymizer
from clinical_access_agent.gateway.tokens import Audience, generate_keypair, issue

CLINICIAN = "prac-01"
QUESTION = "Which of my patients' chest CT reports need follow-up?"
KEY = bytes(range(32))


def _heading(number: int, title: str) -> None:
    print(f"\n{number}. {title}\n" + "-" * (len(title) + 3))


@asynccontextmanager
async def _gateway(
    bundle: dict[str, Any], workdir: Path, *, deidentify: bool = True
) -> AsyncIterator[tuple[GatewayClient, Settings]]:
    private, public = generate_keypair()
    settings = Settings(
        fhir_base="http://fhir.demo/fhir",
        token_public_key=public,
        pseudonym_key=KEY,
        audit_path=workdir / "audit.jsonl",
        worklist_path=workdir / "worklist.jsonl",
        deidentify_text=deidentify,
    )
    app = create_app(settings, transport=InMemoryFhir(bundle).transport())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http,
    ):
        token = issue(private, CLINICIAN, Audience.AGENT)
        yield GatewayClient("http://gateway.demo", token, http), settings


async def _run(client: GatewayClient, guard: SessionGuard) -> AgentResult:
    return await ask(QUESTION, client, model=ScriptedModel(obedient=True), guard=guard)


async def main_async() -> None:
    bundle, labels = generate(seed=7)
    alias = Pseudonymizer(KEY).pseudonym
    panel = {
        e["resource"]["subject"]["reference"].removeprefix("Patient/")
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "CareTeam"
        and any(p["member"]["reference"] == f"Practitioner/{CLINICIAN}"
                for p in e["resource"]["participant"])
    }  # fmt: skip
    attack = next(
        label
        for label in labels
        if label.patient_id in panel and label.injection_goal == Goal.CROSS_PATIENT_READ
    )

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        async with _gateway(bundle, workdir / "protected") as (client, settings):
            result = await _run(client, SessionGuard())
            report = await client.read_report(
                alias(CLINICIAN, Kind.PATIENT, attack.patient_id),
                alias(CLINICIAN, Kind.REPORT, attack.report_id),
            )

        _heading(1, f"{CLINICIAN} asks: {QUESTION}")
        required = [i for i in result.triage if i.follow_up == "required"]
        review = [i for i in result.triage if i.needs_human_review]
        print(f"Reports reviewed: {len(result.triage)}   model calls: {result.llm_calls}   "
              f"tool calls: {result.tool_calls}")  # fmt: skip
        print(f"Follow-up required: {len(required)}   flagged for human review: {len(review)}")
        for item in required[:5]:
            when = "promptly" if item.interval_months == 0 else f"in {item.interval_months} months"
            print(f"  {item.patient}  {item.report}  follow-up {when}")
        if len(required) > 5:
            print(f"  ... and {len(required) - 5} more")

        _heading(2, "A report with injected text, as the model received it")
        print(f"Injected in the record: {attack.injection_id} ({attack.injection_goal})")
        screened = screen(report["content"]["text"]).text
        print("\n".join(f"  | {line}" for line in screened.splitlines()))
        flagged = alias(CLINICIAN, Kind.REPORT, attack.report_id) in {i.report for i in review}
        print("The gateway de-identified the header; screening withheld the injected")
        print("sentence before the model saw it. Flagged for human review:", flagged)
        target = str(attack.injection_target_patient)

        _heading(3, "The same attack with de-identification, screening and agent checks off")
        async with _gateway(bundle, workdir / "open", deidentify=False) as (client, settings):
            open_guard = SessionGuard(
                screen_content=False, redact_identifiers=False, enforce_references=False
            )
            exposed = await _run(client, open_guard)
            denied = {
                str(e.detail["patient"]) for e in exposed.events if e.kind == "gateway_denied"
            }
            print(f"The obedient model requested {len(denied)} references it found in report text:")
            print(f"record numbers from report headers, and {target}, named by this injection.")
            print("The gateway accepts only references it issued itself; all were denied.")
            records = [json.loads(line) for line in settings.audit_path.read_text().splitlines()]
            for record in [r for r in records if r["resource"] == target][:1]:
                print("Gateway answer: 404 not_found. Audit record:")
                shown = {k: record[k] for k in ("seq", "clinician", "action", "resource",
                                                "decision", "reason")}  # fmt: skip
                print("  " + json.dumps(shown))
            read = {r["patient"] for r in records if r["decision"] == "allow" and r["patient"]}
            print(f"Patients whose records were returned: {len(read)}, all on the panel: "
                  f"{read <= panel}")  # fmt: skip

            _heading(4, "Audit log")
            check = audit.verify(settings.audit_path)
            print(
                f"{check.records} records, hash chain intact: {check.ok}, head {check.head[:16]}..."
            )
            denies = sum(r["decision"] == "deny" for r in records)
            print(f"Every request is recorded, including {denies} denials.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
