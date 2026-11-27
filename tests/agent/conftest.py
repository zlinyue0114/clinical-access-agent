from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from clinical_access_agent.agent.client import GatewayClient
from clinical_access_agent.data.generate import Label, generate
from clinical_access_agent.data.memory_fhir import InMemoryFhir
from clinical_access_agent.gateway.app import create_app
from clinical_access_agent.gateway.config import Settings
from clinical_access_agent.gateway.pseudonym import Kind, Pseudonymizer
from clinical_access_agent.gateway.tokens import Audience, generate_keypair, issue

KEY = bytes(range(32))
CLINICIAN = "prac-01"


@dataclass
class Stack:
    bundle: dict[str, Any]
    labels: list[Label]
    settings: Settings
    private_key: bytes
    token: str

    def audit(self) -> list[dict[str, Any]]:
        import json

        if not self.settings.audit_path.exists():
            return []
        return [json.loads(line) for line in self.settings.audit_path.read_text().splitlines()]

    def panel(self, clinician: str = CLINICIAN) -> set[str]:
        member = f"Practitioner/{clinician}"
        return {
            e["resource"]["subject"]["reference"].removeprefix("Patient/")
            for e in self.bundle["entry"]
            if e["resource"]["resourceType"] == "CareTeam"
            and any(p["member"]["reference"] == member for p in e["resource"]["participant"])
        }

    def report_alias(self, report_id: str, clinician: str = CLINICIAN) -> str:
        return Pseudonymizer(KEY).pseudonym(clinician, Kind.REPORT, report_id)


@pytest.fixture(scope="session")
def dataset() -> tuple[dict[str, Any], list[Label]]:
    return generate(seed=7)


@pytest.fixture
def stack(tmp_path: Path, dataset: tuple[dict[str, Any], list[Label]]) -> Stack:
    private, public = generate_keypair()
    settings = Settings(
        fhir_base="http://fhir.test/fhir",
        token_public_key=public,
        pseudonym_key=KEY,
        audit_path=tmp_path / "audit.jsonl",
        worklist_path=tmp_path / "worklist.jsonl",
    )
    return Stack(
        bundle=dataset[0],
        labels=dataset[1],
        settings=settings,
        private_key=private,
        token=issue(private, CLINICIAN, Audience.AGENT),
    )


Connect = Callable[..., AbstractAsyncContextManager[GatewayClient]]


@pytest.fixture
def connect(stack: Stack) -> Connect:
    """An agent-side gateway client wired to an in-process gateway and FHIR server.

    Keyword arguments override gateway settings, e.g. ``deidentify_text=False``.
    """

    @asynccontextmanager
    async def open_client(**overrides: Any) -> AsyncIterator[GatewayClient]:
        settings = replace(stack.settings, **overrides)
        app = create_app(settings, transport=InMemoryFhir(stack.bundle).transport())
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http,
        ):
            yield GatewayClient("http://gateway.test", stack.token, http)

    return open_client
