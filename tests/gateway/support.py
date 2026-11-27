"""Shared test types: the dataset view, a controllable clock, client helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from clinical_access_agent.data.generate import Label
from clinical_access_agent.gateway.pseudonym import Kind


@dataclass
class World:
    """The generated dataset plus the access relation computed directly from it."""

    bundle: dict[str, Any]
    labels: list[Label]
    clinicians: list[str] = field(default_factory=list)
    patients: list[str] = field(default_factory=list)
    panels: dict[str, set[str]] = field(default_factory=dict)
    reports: dict[str, list[str]] = field(default_factory=dict)

    def resources(self, kind: str) -> list[dict[str, Any]]:
        return [
            e["resource"] for e in self.bundle["entry"] if e["resource"]["resourceType"] == kind
        ]

    def resource(self, kind: str, resource_id: str) -> dict[str, Any]:
        return next(r for r in self.resources(kind) if r["id"] == resource_id)


@dataclass
class Clock:
    wall: datetime
    mono: float = 1000.0

    def now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


MakeClient = Callable[..., TestClient]
Auth = Callable[..., dict[str, str]]
Alias = Callable[[str, Kind, str], str]
