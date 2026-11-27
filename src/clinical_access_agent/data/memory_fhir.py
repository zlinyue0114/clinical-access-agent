"""A minimal read-only FHIR server in memory, for offline tests and evaluations.

It serves a generated transaction bundle through an ``httpx.MockTransport`` and
supports exactly the interactions the gateway uses:

- ``GET [base]/{type}/{id}``
- ``GET [base]/CareTeam?participant=Practitioner/{id}``
- ``GET [base]/DiagnosticReport?subject=Patient/{id}``
- ``GET [base]/Patient?_id={id},{id},...``

Anything else is answered with an error, so that a gateway change relying on an
unsupported interaction fails loudly in tests instead of silently passing.

``writable=True`` accepts ``PUT [base]/{type}/{id}`` and records it. Only the
deliberately vulnerable evaluation baseline uses it, to show what an agent with
direct write access does with injected instructions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

_SEARCH_PARAMS: dict[str, set[str]] = {
    "CareTeam": {"participant"},
    "DiagnosticReport": {"subject"},
    "Patient": {"_id", "identifier"},
}
_IGNORED_PARAMS = {"_count"}


class InMemoryFhir:
    """Serve the resources of one transaction bundle."""

    def __init__(self, bundle: dict[str, Any], *, writable: bool = False) -> None:
        self._resources: dict[str, dict[str, dict[str, Any]]] = {}
        for entry in bundle.get("entry", []):
            resource = entry["resource"]
            self._resources.setdefault(resource["resourceType"], {})[resource["id"]] = resource
        self.unavailable = False
        self.writable = writable
        self.requests: list[httpx.Request] = []
        self.writes: list[dict[str, Any]] = []

    @classmethod
    def from_file(cls, path: Path) -> InMemoryFhir:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unavailable:
            return httpx.Response(503, json=_outcome("service unavailable"))
        segments = [s for s in request.url.path.split("/") if s]
        if request.method == "PUT" and self.writable and len(segments) >= 2:
            resource = json.loads(request.content)
            self._resources.setdefault(segments[-2], {})[segments[-1]] = resource
            self.writes.append(resource)
            return httpx.Response(200, json=resource)
        if request.method != "GET":
            return httpx.Response(405, json=_outcome("read-only server"))

        if len(segments) >= 2 and segments[-2] in self._resources:
            resource = self._resources[segments[-2]].get(segments[-1])
            if resource is None:
                return httpx.Response(404, json=_outcome("not found"))
            return httpx.Response(200, json=resource)
        if segments and segments[-1] in _SEARCH_PARAMS:
            return self._search(segments[-1], request)
        return httpx.Response(400, json=_outcome("unsupported interaction"))

    def _search(self, kind: str, request: httpx.Request) -> httpx.Response:
        params = {k: v for k, v in request.url.params.multi_items() if k not in _IGNORED_PARAMS}
        if not params or set(params) - _SEARCH_PARAMS[kind]:
            return httpx.Response(400, json=_outcome("unsupported search parameter"))
        matches = [r for r in self._resources.get(kind, {}).values() if _matches(r, params)]
        bundle = {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": len(matches),
            "entry": [{"resource": r, "search": {"mode": "match"}} for r in matches],
        }
        return httpx.Response(200, json=bundle)


def _matches(resource: dict[str, Any], params: dict[str, str]) -> bool:
    return all(_value_matches(resource, name, value) for name, value in params.items())


def _value_matches(resource: dict[str, Any], name: str, value: str) -> bool:
    if name == "_id":
        return bool(resource["id"] in value.split(","))
    if name == "participant":
        return value in {
            p.get("member", {}).get("reference") for p in resource.get("participant", [])
        }
    if name == "identifier":
        return value in {i.get("value") for i in resource.get("identifier", [])}
    if name == "subject":
        return bool(resource.get("subject", {}).get("reference") == value)
    return False


def _outcome(message: str) -> dict[str, Any]:
    return {
        "resourceType": "OperationOutcome",
        "issue": [{"severity": "error", "code": "processing", "diagnostics": message}],
    }
