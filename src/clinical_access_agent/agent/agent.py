"""Build and run the follow-up review agent.

One agent instance serves one clinician session: its tools are bound to a gateway
client that holds that clinician's agent token, and its guard holds the references
issued in that session. Nothing is shared between sessions.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Literal

from google.adk.agents import LlmAgent, RunConfig
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from clinical_access_agent.agent.client import GatewayClient
from clinical_access_agent.agent.guard import Event, SessionGuard
from clinical_access_agent.agent.prompt import INSTRUCTION
from clinical_access_agent.agent.telemetry import disable_content_capture
from clinical_access_agent.agent.tools import TriageItem, TriageRecord, make_tools

DEFAULT_MODEL = "gemini-3.8-flash"
MAX_LLM_CALLS = 20
APP_NAME = "clinical_access_agent"


@dataclass
class AgentResult:
    answer: str
    status: Literal["completed", "llm_call_limit"]
    triage: list[TriageItem]
    triage_submitted: bool
    llm_calls: int
    tool_calls: int
    events: list[Event] = field(default_factory=list)


@dataclass
class _Counter:
    calls: int = 0

    def before_model(
        self, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        self.calls += 1
        return None


def default_model() -> str:
    return os.environ.get("CAA_MODEL", DEFAULT_MODEL)


def build_agent(
    client: GatewayClient,
    guard: SessionGuard,
    record: TriageRecord,
    *,
    model: str | BaseLlm | None = None,
    counter: _Counter | None = None,
) -> LlmAgent:
    disable_content_capture()
    return LlmAgent(
        name="follow_up_reviewer",
        description="Reviews chest CT reports of the clinician's own patients for follow-up.",
        model=model or default_model(),
        instruction=INSTRUCTION,
        tools=list(make_tools(client, record)),
        before_model_callback=(counter or _Counter()).before_model,
        before_tool_callback=guard.before_tool,
        after_tool_callback=guard.after_tool,
        after_model_callback=guard.after_model,
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
    )


async def ask(
    question: str,
    client: GatewayClient,
    *,
    model: str | BaseLlm | None = None,
    max_llm_calls: int = MAX_LLM_CALLS,
    guard: SessionGuard | None = None,
) -> AgentResult:
    """Run one question in a fresh session and collect the result and the guard log."""
    guard = guard or SessionGuard()
    record = TriageRecord()
    counter = _Counter()
    agent = build_agent(client, guard, record, model=model, counter=counter)
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    user_id = f"session-{uuid.uuid4().hex}"
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id=user_id)

    answer, status = "", "completed"
    message = types.Content(role="user", parts=[types.Part(text=question)])
    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=message,
            run_config=RunConfig(max_llm_calls=max_llm_calls),
        ):
            if event.is_final_response() and event.content and event.content.parts:
                answer = "".join(p.text or "" for p in event.content.parts)
    except LlmCallsLimitExceededError:
        status = "llm_call_limit"
        guard.events.append(Event("llm_call_limit"))
    finally:
        await runner.close()

    return AgentResult(
        answer=answer,
        status=status,  # type: ignore[arg-type]
        triage=record.items,
        triage_submitted=record.submitted,
        llm_calls=counter.calls,
        tool_calls=guard.tool_calls,
        events=guard.events,
    )
