"""HTTP layer of the gateway.

The audit middleware wraps every request, including unknown routes, rejected
methods, validation errors and unexpected exceptions, and writes exactly one audit
record before the response leaves the gateway. If the record cannot be written, the
client receives ``503`` instead of the data.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from clinical_access_agent import __version__
from clinical_access_agent.gateway.actions import ConfirmationStore, Worklist
from clinical_access_agent.gateway.audit import AuditLog
from clinical_access_agent.gateway.config import Settings
from clinical_access_agent.gateway.errors import GatewayError, Reason
from clinical_access_agent.gateway.fhir import FhirClient, FhirUnavailableError
from clinical_access_agent.gateway.models import (
    CommitAction,
    PatientIdentity,
    PatientSummary,
    PreparedAction,
    PrepareFollowUp,
    ReportDetail,
    ReportSummary,
    WorklistItem,
)
from clinical_access_agent.gateway.pseudonym import PseudonymCollisionError, Pseudonymizer
from clinical_access_agent.gateway.quota import Quota
from clinical_access_agent.gateway.service import AuditContext, GatewayService
from clinical_access_agent.gateway.tokens import Audience, Principal, TokenError, verify

_STATUS_REASON = {
    400: Reason.BAD_REQUEST,
    401: Reason.INVALID_TOKEN,
    403: Reason.WRONG_AUDIENCE,
    404: Reason.NOT_FOUND,
    405: Reason.METHOD_NOT_ALLOWED,
    409: Reason.CONFIRMATION_INVALID,
    429: Reason.QUOTA_EXCEEDED,
    503: Reason.UPSTREAM_UNAVAILABLE,
}


def _error(status: int, reason: Reason) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": reason.value})


def create_app(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> FastAPI:
    """Build the gateway. ``transport`` and the clocks are injectable for tests."""
    clock = now or (lambda: datetime.now(UTC))
    audit = AuditLog(settings.audit_path, now=clock)
    quota_args = {} if monotonic is None else {"clock": monotonic}
    quota = Quota(
        max_requests_per_minute=settings.max_requests_per_minute,
        max_patients_per_hour=settings.max_patients_per_hour,
        **quota_args,
    )
    pseudonyms = Pseudonymizer(settings.pseudonym_key)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            transport=transport, timeout=settings.fhir_timeout_seconds
        ) as http:
            app.state.service = GatewayService(
                fhir=FhirClient(settings.fhir_base, http),
                pseudonyms=pseudonyms,
                quota=quota,
                confirmations=ConfirmationStore(ttl=settings.confirmation_ttl, now=clock),
                worklist=Worklist(settings.worklist_path, now=clock),
                today=lambda: clock().date(),
            )
            yield

    app = FastAPI(
        title="clinical-access-agent EHR gateway",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.audit = audit

    @app.middleware("http")
    async def audit_every_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        ctx = AuditContext(request_id=uuid.uuid4().hex)
        request.state.audit = ctx
        try:
            response = await call_next(request)
        except Exception:
            ctx.reason = Reason.INTERNAL_ERROR
            response = _error(500, Reason.INTERNAL_ERROR)
        route = request.scope.get("route")
        reason = ctx.reason or (
            Reason.OK if response.status_code < 400 else _STATUS_REASON.get(response.status_code)
        )
        try:
            audit.append(
                request_id=ctx.request_id,
                clinician=ctx.clinician,
                audience=ctx.audience,
                method=request.method,
                route=getattr(route, "path", "unmatched"),
                action=getattr(route, "name", "unmatched"),
                resource=ctx.resource,
                patient=ctx.patient,
                status=response.status_code,
                reason=(reason or Reason.INTERNAL_ERROR).value,
            )
        except Exception:
            return _error(503, Reason.INTERNAL_ERROR)
        response.headers["X-Request-ID"] = ctx.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        request.state.audit.reason = exc.reason
        return _error(exc.status, exc.reason)

    @app.exception_handler(FhirUnavailableError)
    async def fhir_unavailable(request: Request, exc: FhirUnavailableError) -> JSONResponse:
        request.state.audit.reason = Reason.UPSTREAM_UNAVAILABLE
        return _error(503, Reason.UPSTREAM_UNAVAILABLE)

    @app.exception_handler(PseudonymCollisionError)
    async def collision(request: Request, exc: PseudonymCollisionError) -> JSONResponse:
        request.state.audit.reason = Reason.INTERNAL_ERROR
        return _error(500, Reason.INTERNAL_ERROR)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        request.state.audit.reason = Reason.BAD_REQUEST
        return _error(400, Reason.BAD_REQUEST)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        reason = _STATUS_REASON.get(exc.status_code, Reason.BAD_REQUEST)
        request.state.audit.reason = reason
        return _error(exc.status_code, reason)

    def authenticate(
        request: Request, authorization: Annotated[str | None, Header()] = None
    ) -> Principal:
        ctx: AuditContext = request.state.audit
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise GatewayError(401, Reason.INVALID_TOKEN)
        try:
            who = verify(token.strip(), settings.token_public_key, issuer=settings.token_issuer)
        except TokenError as exc:
            raise GatewayError(401, Reason.INVALID_TOKEN) from exc
        ctx.clinician, ctx.audience = who.clinician_id, who.audience.value
        quota.check_request(who.clinician_id)
        return who

    def require(*audiences: Audience) -> Callable[[Principal], Principal]:
        # Defaults, not Annotated: the dependency is local to create_app and string
        # annotations would not resolve it.
        def check(who: Principal = Depends(authenticate)) -> Principal:  # noqa: B008
            if who.audience not in audiences:
                raise GatewayError(403, Reason.WRONG_AUDIENCE)
            return who

        return check

    reader = Depends(require(Audience.AGENT, Audience.UI))
    ui_only = Depends(require(Audience.UI))

    def service(request: Request) -> GatewayService:
        result: GatewayService = request.app.state.service
        return result

    svc = Depends(service)

    @app.get("/healthz", name="health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/patients", name="patients.list")
    async def list_patients(
        who: Principal = reader, gateway: GatewayService = svc
    ) -> list[PatientSummary]:
        return await gateway.list_patients(who)

    @app.get("/v1/patients/{patient}/reports", name="reports.list")
    async def list_reports(
        patient: str, request: Request, who: Principal = reader, gateway: GatewayService = svc
    ) -> list[ReportSummary]:
        ctx: AuditContext = request.state.audit
        ctx.resource = patient
        return await gateway.list_reports(who, patient, ctx)

    @app.get("/v1/patients/{patient}/reports/{report}", name="reports.read")
    async def read_report(
        patient: str,
        report: str,
        request: Request,
        who: Principal = reader,
        gateway: GatewayService = svc,
    ) -> ReportDetail:
        ctx: AuditContext = request.state.audit
        ctx.resource = f"{patient}/{report}"
        return await gateway.get_report(who, patient, report, ctx)

    @app.get("/v1/patients/{patient}/identity", name="patients.reidentify")
    async def reidentify(
        patient: str, request: Request, who: Principal = ui_only, gateway: GatewayService = svc
    ) -> PatientIdentity:
        ctx: AuditContext = request.state.audit
        ctx.resource = patient
        return await gateway.identity(who, patient, ctx)

    @app.post("/v1/actions/follow-up/prepare", name="follow_up.prepare")
    async def prepare(
        body: PrepareFollowUp,
        request: Request,
        who: Principal = reader,
        gateway: GatewayService = svc,
    ) -> PreparedAction:
        ctx: AuditContext = request.state.audit
        ctx.resource = f"{body.patient}/{body.report}"
        return await gateway.prepare_follow_up(who, body, ctx)

    @app.post("/v1/actions/follow-up/commit", name="follow_up.commit")
    async def commit(
        body: CommitAction,
        request: Request,
        who: Principal = ui_only,
        gateway: GatewayService = svc,
    ) -> WorklistItem:
        return await gateway.commit_follow_up(who, body, request.state.audit)

    return app
