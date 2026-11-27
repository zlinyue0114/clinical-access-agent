"""HTTP client for the EHR gateway, as used by the agent's tools.

The session token is held here, inside the runtime. It is never a tool argument,
never part of a tool result and never placed in session state, so the model cannot
read, alter or replay it.
"""

from __future__ import annotations

from typing import Any

import httpx

ERROR_CODES = {
    400: "bad_request",
    401: "invalid_token",
    403: "wrong_audience",
    404: "not_found",
    409: "confirmation_invalid",
    429: "quota_exceeded",
    503: "upstream_unavailable",
}


class GatewayClient:
    def __init__(self, base_url: str, token: str, http: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http

    async def list_patients(self) -> dict[str, Any]:
        return await self._call("GET", "/v1/patients", key="patients")

    async def list_reports(self, patient: str) -> dict[str, Any]:
        return await self._call("GET", f"/v1/patients/{patient}/reports", key="reports")

    async def read_report(self, patient: str, report: str) -> dict[str, Any]:
        return await self._call("GET", f"/v1/patients/{patient}/reports/{report}")

    async def prepare_follow_up(
        self, patient: str, report: str, interval_months: int
    ) -> dict[str, Any]:
        body = {"patient": patient, "report": report, "interval_months": interval_months}
        return await self._call("POST", "/v1/actions/follow-up/prepare", json=body)

    async def _call(
        self, method: str, path: str, *, key: str | None = None, json: Any = None
    ) -> dict[str, Any]:
        """Return the gateway's JSON, or ``{"error": code}``. Never raises on HTTP errors."""
        try:
            response = await self._http.request(
                method, f"{self._base}{path}", headers=self._headers, json=json
            )
        except httpx.HTTPError:
            return {"error": "gateway_unreachable"}
        if response.status_code != 200:
            return {"error": ERROR_CODES.get(response.status_code, "gateway_error")}
        body = response.json()
        if key is not None:
            return {key: body}
        return body if isinstance(body, dict) else {"result": body}
