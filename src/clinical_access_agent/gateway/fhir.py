"""Read-only FHIR client used by the gateway.

Every failure (transport error, timeout, unexpected status, malformed body, more
pages than allowed) raises ``FhirUnavailableError``. Callers treat that as a denial,
never as an empty result.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_PAGES = 20
PAGE_SIZE = 200
_FHIR_ID = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")


class FhirUnavailableError(Exception):
    """The FHIR server could not give a trustworthy answer."""


class FhirClient:
    def __init__(self, base_url: str, http: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._http = http

    async def read(self, resource_type: str, resource_id: str) -> dict[str, Any] | None:
        """Return the resource, or ``None`` if the server reports it does not exist."""
        if not _FHIR_ID.match(resource_id):
            return None
        response = await self._get(f"{self._base}/{resource_type}/{resource_id}", None)
        if response.status_code in (404, 410):
            return None
        body = _json(response)
        if body.get("resourceType") != resource_type or body.get("id") != resource_id:
            raise FhirUnavailableError("unexpected resource in read response")
        return body

    async def search(self, resource_type: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """Return all matching resources, following paging links up to ``MAX_PAGES``."""
        query: dict[str, str] | None = {**params, "_count": str(PAGE_SIZE)}
        url = f"{self._base}/{resource_type}"
        found: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            bundle = _json(await self._get(url, query))
            if bundle.get("resourceType") != "Bundle":
                raise FhirUnavailableError("search did not return a Bundle")
            for entry in bundle.get("entry", []):
                resource = entry.get("resource", {})
                if resource.get("resourceType") == resource_type:
                    found.append(resource)
            next_url = _next_link(bundle)
            if next_url is None:
                return found
            # Paging links may carry the server's internal host name; keep our base.
            url, query = f"{self._base}?{urlsplit(next_url).query}", None
        raise FhirUnavailableError("result set exceeds the page limit")

    async def _get(self, url: str, params: dict[str, str] | None) -> httpx.Response:
        try:
            response = await self._http.get(
                url, params=params, headers={"Accept": "application/fhir+json"}
            )
        except httpx.HTTPError as exc:
            raise FhirUnavailableError(type(exc).__name__) from exc
        if response.status_code not in (200, 404, 410):
            raise FhirUnavailableError(f"status {response.status_code}")
        return response


def _json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code != 200:
        raise FhirUnavailableError(f"status {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise FhirUnavailableError("invalid JSON") from exc
    if not isinstance(body, dict):
        raise FhirUnavailableError("unexpected JSON")
    return body


def _next_link(bundle: dict[str, Any]) -> str | None:
    for link in bundle.get("link", []):
        if link.get("relation") == "next" and isinstance(link.get("url"), str):
            return str(link["url"])
    return None
