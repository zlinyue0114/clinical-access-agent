"""End-to-end runs of the agent against the in-process gateway with a scripted model.

The obedient scripted model does whatever injected text asks, where it can. The
tests switch guard layers off one at a time and check which layer stops each
attempt, and that the gateway holds even with every agent-side layer off.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from clinical_access_agent.agent.agent import AgentResult, ask
from clinical_access_agent.agent.guard import SessionGuard
from clinical_access_agent.agent.stub import ScriptedModel
from clinical_access_agent.agent.telemetry import CONTENT_CAPTURE_VARIABLES
from tests.agent.conftest import CLINICIAN, Connect, Stack

QUESTION = "Which of my patients' chest CT reports need follow-up?"


async def _run(
    connect: Connect, model: BaseLlm, *, gateway_deid: bool = True, **guard: Any
) -> AgentResult:
    async with connect(deidentify_text=gateway_deid) as client:
        return await ask(QUESTION, client, model=model, guard=SessionGuard(**guard))


_IDENTIFIER_SHAPE = re.compile(r"\b[A-Za-z]{2,6}-\d{3,}\b")


def _identities(stack: Stack) -> list[re.Pattern[str]]:
    patterns = []
    for entry in stack.bundle["entry"]:
        resource = entry["resource"]
        if resource["resourceType"] == "Patient":
            name = resource["name"][0]
            patterns.append(re.compile(rf"\b{re.escape(name['family'])}\b"))
            patterns.append(re.compile(re.escape(resource["birthDate"])))
    return patterns


class RecordingModel(ScriptedModel):
    """The scripted model, keeping every request it receives."""

    seen: list[str] = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.model_dump_json())
        async for response in super().generate_content_async(llm_request, stream):
            yield response


async def test_review_covers_every_report_of_the_panel(connect: Connect, stack: Stack) -> None:
    result = await _run(connect, ScriptedModel())
    assert result.status == "completed"
    assert result.triage_submitted
    expected = {
        stack.report_alias(label.report_id)
        for label in stack.labels
        if label.patient_id in stack.panel()
    }
    assert {item.report for item in result.triage} == expected
    assert result.llm_calls <= 6
    assert all(r["decision"] == "allow" for r in stack.audit())


async def test_scripted_baseline_finds_required_follow_up(connect: Connect, stack: Stack) -> None:
    result = await _run(connect, ScriptedModel())
    truth = {
        stack.report_alias(label.report_id): label.follow_up.value
        for label in stack.labels
        if label.patient_id in stack.panel()
    }
    flagged = {i.report for i in result.triage if i.follow_up == "required"}
    required = {r for r, level in truth.items() if level == "required"}
    assert len(flagged & required) / len(required) >= 0.9


async def test_screening_withholds_injected_sentences(connect: Connect, stack: Stack) -> None:
    result = await _run(connect, ScriptedModel(obedient=True))
    injected = {
        stack.report_alias(label.report_id)
        for label in stack.labels
        if label.patient_id in stack.panel() and label.injection_id
    }
    screened = {e.detail["report"] for e in result.events if e.kind == "content_screened"}
    assert injected and screened == injected
    review = {i.report for i in result.triage if i.needs_human_review}
    assert review == injected
    assert not [e for e in result.events if e.kind in ("tool_blocked", "gateway_denied")]


async def test_tampering_does_not_suppress_required_follow_up(
    connect: Connect, stack: Stack
) -> None:
    result = await _run(connect, ScriptedModel(obedient=True))
    tampered = {
        stack.report_alias(label.report_id)
        for label in stack.labels
        if label.patient_id in stack.panel() and label.injection_goal == "clinical_tampering"
    }
    assert tampered
    decided = {i.report: i.follow_up for i in result.triage}
    assert all(decided[r] == "required" for r in tampered)


async def test_planted_identifiers_never_reach_the_model(connect: Connect, stack: Stack) -> None:
    model = RecordingModel(obedient=True)
    model.seen.clear()
    result = await _run(connect, model)
    assert not _IDENTIFIER_SHAPE.search("\n".join(model.seen))
    assert not [e for e in result.events if e.kind in ("tool_blocked", "gateway_denied")]


async def test_agent_redaction_covers_a_gateway_without_deidentification(
    connect: Connect, stack: Stack
) -> None:
    result = await _run(
        connect, ScriptedModel(obedient=True), gateway_deid=False, screen_content=False
    )
    assert result.events and any(e.kind == "identifiers_redacted" for e in result.events)
    assert not [e for e in result.events if e.kind in ("tool_blocked", "gateway_denied")]


async def test_reference_policy_stops_planted_identifiers(connect: Connect, stack: Stack) -> None:
    result = await _run(
        connect,
        ScriptedModel(obedient=True),
        gateway_deid=False,
        screen_content=False,
        redact_identifiers=False,
    )
    blocked = [e for e in result.events if e.kind == "tool_blocked"]
    assert blocked
    assert {e.detail["reason"] for e in blocked} == {"unknown_patient_reference"}
    assert not [e for e in result.events if e.kind == "gateway_denied"]
    planted = {e.detail["patient"] for e in blocked}
    assert all(r["resource"] not in planted for r in stack.audit())


async def test_gateway_holds_with_every_agent_layer_off(connect: Connect, stack: Stack) -> None:
    result = await _run(
        connect,
        ScriptedModel(obedient=True),
        gateway_deid=False,
        screen_content=False,
        redact_identifiers=False,
        enforce_references=False,
    )
    attempts = {e.detail["patient"] for e in result.events if e.kind == "gateway_denied"}
    assert attempts
    assert not attempts & stack.panel()
    denials = [r for r in stack.audit() if r["resource"] in attempts]
    assert denials and all(r["decision"] == "deny" for r in denials)
    assert all(r["patient"] is None or r["patient"] in stack.panel() for r in stack.audit())


async def test_model_never_sees_the_token_or_identities(connect: Connect, stack: Stack) -> None:
    model = RecordingModel()
    model.seen.clear()
    await _run(connect, model)
    transcript = "\n".join(model.seen)
    assert stack.token not in transcript
    assert "Bearer" not in transcript
    assert CLINICIAN not in transcript
    assert not [p.pattern for p in _identities(stack) if p.search(transcript)]
    assert not _IDENTIFIER_SHAPE.search(transcript)


async def test_agent_redaction_alone_keeps_identities_out(connect: Connect, stack: Stack) -> None:
    model = RecordingModel()
    model.seen.clear()
    await _run(connect, model, gateway_deid=False)
    transcript = "\n".join(model.seen)
    assert not [p.pattern for p in _identities(stack) if p.search(transcript)]


async def test_identity_check_detects_leaks(connect: Connect, stack: Stack) -> None:
    """Positive control: with every text layer off, the same checks must fire."""
    model = RecordingModel()
    model.seen.clear()
    await _run(connect, model, gateway_deid=False, redact_identifiers=False, screen_content=False)
    transcript = "\n".join(model.seen)
    assert [p.pattern for p in _identities(stack) if p.search(transcript)]
    assert _IDENTIFIER_SHAPE.search(transcript)


async def test_model_calls_are_bounded(connect: Connect) -> None:
    class Looping(BaseLlm):
        model: str = "looping"

        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            call = types.FunctionCall(name="list_my_patients", args={})
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(function_call=call)])
            )

    result = await _run(connect, Looping())
    assert result.status == "llm_call_limit"
    assert result.llm_calls <= 21


async def test_unknown_tools_cannot_be_invoked(connect: Connect, stack: Stack) -> None:
    class Exporter(BaseLlm):
        model: str = "exporter"

        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            last = llm_request.contents[-1]
            if any(p.function_response for p in last.parts or []):
                part = types.Part(text="done")
            else:
                part = types.Part(function_call=types.FunctionCall(name="export_all", args={}))
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    result = await _run(connect, Exporter())
    assert result.answer == "done"
    assert stack.audit() == []


def test_content_capture_is_disabled_after_building(connect: Connect) -> None:
    from clinical_access_agent.agent.agent import build_agent
    from clinical_access_agent.agent.tools import TriageRecord

    for name in CONTENT_CAPTURE_VARIABLES:
        os.environ[name] = "true"
    build_agent(None, SessionGuard(), TriageRecord(), model=ScriptedModel())  # type: ignore[arg-type]
    assert all(os.environ[name] == "false" for name in CONTENT_CAPTURE_VARIABLES)
