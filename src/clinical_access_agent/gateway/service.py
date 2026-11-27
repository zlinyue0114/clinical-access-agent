"""Gateway operations, independent of HTTP.

Each operation receives the verified ``Principal`` and an ``AuditContext`` that it
fills in (resolved patient, outcome reason). The HTTP layer writes the audit record.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from clinical_access_agent.gateway.actions import ConfirmationStore, Worklist
from clinical_access_agent.gateway.authz import load_panel
from clinical_access_agent.gateway.config import MRN_SYSTEM
from clinical_access_agent.gateway.errors import Reason, not_found
from clinical_access_agent.gateway.fhir import FhirClient
from clinical_access_agent.gateway.models import (
    CommitAction,
    PatientIdentity,
    PatientSummary,
    PreparedAction,
    PrepareFollowUp,
    ReportDetail,
    ReportSummary,
    UntrustedText,
    WorklistItem,
)
from clinical_access_agent.gateway.pseudonym import Kind, Pseudonymizer
from clinical_access_agent.gateway.quota import Quota
from clinical_access_agent.gateway.tokens import Principal

MAX_REPORT_CHARS = 20_000


@dataclass
class AuditContext:
    request_id: str
    clinician: str | None = None
    audience: str | None = None
    resource: str | None = None
    patient: str | None = None
    reason: Reason | None = None


class GatewayService:
    def __init__(
        self,
        *,
        fhir: FhirClient,
        pseudonyms: Pseudonymizer,
        quota: Quota,
        confirmations: ConfirmationStore,
        worklist: Worklist,
        today: Callable[[], date],
    ) -> None:
        self._fhir = fhir
        self._pseudonyms = pseudonyms
        self._quota = quota
        self._confirmations = confirmations
        self._worklist = worklist
        self._today = today

    async def list_patients(self, who: Principal) -> list[PatientSummary]:
        panel = await load_panel(self._fhir, who.clinician_id)
        if not panel.patient_ids:
            return []
        ids = sorted(panel.patient_ids)
        resources = await self._fhir.search("Patient", {"_id": ",".join(ids)})
        found = {r["id"]: r for r in resources if r.get("id") in panel.patient_ids}
        summaries = [
            PatientSummary(
                patient=self._alias(who, Kind.PATIENT, pid),
                sex=str(found[pid].get("gender", "unknown")),
                age_band=_age_band(found[pid].get("birthDate"), self._today()),
                language=_preferred_language(found[pid]),
            )
            for pid in ids
            if pid in found
        ]
        return sorted(summaries, key=lambda s: s.patient)

    async def list_reports(
        self, who: Principal, patient_ref: str, ctx: AuditContext
    ) -> list[ReportSummary]:
        patient_id = await self._resolve_patient(who, patient_ref, ctx)
        reports = await self._reports_of(patient_id)
        return [
            ReportSummary(
                report=self._alias(who, Kind.REPORT, r["id"]),
                date=_date(r),
                title=_title(r),
                status=str(r.get("status", "unknown")),
            )
            for r in reports
        ]

    async def get_report(
        self, who: Principal, patient_ref: str, report_ref: str, ctx: AuditContext
    ) -> ReportDetail:
        patient_id = await self._resolve_patient(who, patient_ref, ctx)
        report = await self._resolve_report(who, patient_id, report_ref)
        return ReportDetail(
            report=report_ref,
            patient=patient_ref,
            date=_date(report),
            title=_title(report),
            status=str(report.get("status", "unknown")),
            language=_report_language(report),
            content=UntrustedText(text=_report_text(report)),
        )

    async def identity(
        self, who: Principal, patient_ref: str, ctx: AuditContext
    ) -> PatientIdentity:
        patient_id = await self._resolve_patient(who, patient_ref, ctx)
        resource = await self._fhir.read("Patient", patient_id)
        if resource is None:
            raise not_found()
        return PatientIdentity(
            patient=patient_ref, name=_display_name(resource), mrn=_identifier(resource, MRN_SYSTEM)
        )

    async def prepare_follow_up(
        self, who: Principal, request: PrepareFollowUp, ctx: AuditContext
    ) -> PreparedAction:
        patient_id = await self._resolve_patient(who, request.patient, ctx)
        report = await self._resolve_report(who, patient_id, request.report)
        pending = self._confirmations.create(
            clinician_id=who.clinician_id,
            patient_id=patient_id,
            report_id=report["id"],
            interval_months=request.interval_months,
        )
        return PreparedAction(
            confirmation_id=pending.confirmation_id,
            digest=pending.digest,
            expires_at=pending.expires_at.isoformat(timespec="seconds"),
            action=pending.action,
            patient=request.patient,
            report=request.report,
            interval_months=pending.interval_months,
        )

    async def commit_follow_up(
        self, who: Principal, request: CommitAction, ctx: AuditContext
    ) -> WorklistItem:
        ctx.resource = request.confirmation_id
        pending = self._confirmations.consume(
            confirmation_id=request.confirmation_id,
            digest=request.digest,
            clinician_id=who.clinician_id,
        )
        ctx.patient = pending.patient_id
        panel = await load_panel(self._fhir, who.clinician_id)
        if pending.patient_id not in panel.patient_ids:
            raise not_found()
        entry = self._worklist.add(pending)
        return WorklistItem(
            entry_id=entry.entry_id,
            patient=self._alias(who, Kind.PATIENT, entry.patient_id),
            report=self._alias(who, Kind.REPORT, entry.report_id),
            interval_months=entry.interval_months,
            created_at=entry.created_at,
        )

    def _alias(self, who: Principal, kind: Kind, resource_id: str) -> str:
        return self._pseudonyms.pseudonym(who.clinician_id, kind, resource_id)

    async def _resolve_patient(self, who: Principal, patient_ref: str, ctx: AuditContext) -> str:
        """Map a pseudonym to a patient on the clinician's panel, or deny."""
        if not Pseudonymizer.is_well_formed(patient_ref, Kind.PATIENT):
            raise not_found()
        panel = await load_panel(self._fhir, who.clinician_id)
        table = self._pseudonyms.lookup(who.clinician_id, Kind.PATIENT, panel.patient_ids)
        patient_id = table.get(patient_ref)
        if patient_id is None:
            raise not_found()
        ctx.patient = patient_id
        self._quota.check_patient(who.clinician_id, patient_id)
        return patient_id

    async def _resolve_report(
        self, who: Principal, patient_id: str, report_ref: str
    ) -> dict[str, Any]:
        if not Pseudonymizer.is_well_formed(report_ref, Kind.REPORT):
            raise not_found()
        reports = {r["id"]: r for r in await self._reports_of(patient_id)}
        table = self._pseudonyms.lookup(who.clinician_id, Kind.REPORT, reports)
        report_id = table.get(report_ref)
        if report_id is None:
            raise not_found()
        return reports[report_id]

    async def _reports_of(self, patient_id: str) -> list[dict[str, Any]]:
        subject = f"Patient/{patient_id}"
        found = await self._fhir.search("DiagnosticReport", {"subject": subject})
        own = [r for r in found if r.get("subject", {}).get("reference") == subject]
        return sorted(own, key=lambda r: (_date(r) or "", r["id"]), reverse=True)


def _date(report: dict[str, Any]) -> str | None:
    value = report.get("effectiveDateTime") or report.get("issued")
    return str(value)[:10] if value else None


def _title(report: dict[str, Any]) -> str:
    code = report.get("code", {})
    text = code.get("text") or next(
        (c.get("display") for c in code.get("coding", []) if c.get("display")), None
    )
    return str(text or "Report")


def _report_language(report: dict[str, Any]) -> str | None:
    for form in report.get("presentedForm", []):
        if form.get("language"):
            return str(form["language"])
    return None


def _report_text(report: dict[str, Any]) -> str:
    for form in report.get("presentedForm", []):
        if form.get("contentType", "").startswith("text/plain") and form.get("data"):
            try:
                text = base64.b64decode(form["data"], validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                continue
            return text[:MAX_REPORT_CHARS]
    return str(report.get("conclusion", ""))[:MAX_REPORT_CHARS]


def _age_band(birth_date: object, today: date) -> str | None:
    if not isinstance(birth_date, str):
        return None
    try:
        born = date.fromisoformat(birth_date[:10])
    except ValueError:
        return None
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    if age < 0:
        return None
    if age >= 90:
        return "90+"
    low = age // 10 * 10
    return f"{low}-{low + 9}"


def _preferred_language(patient: dict[str, Any]) -> str | None:
    for item in patient.get("communication", []):
        for coding in item.get("language", {}).get("coding", []):
            if coding.get("code"):
                return str(coding["code"])
    return None


def _display_name(patient: dict[str, Any]) -> str:
    for name in patient.get("name", []):
        parts = [*name.get("given", []), name.get("family", "")]
        text = " ".join(p for p in parts if p)
        if text:
            return text
    return ""


def _identifier(patient: dict[str, Any], system: str) -> str | None:
    for identifier in patient.get("identifier", []):
        if identifier.get("system") == system and identifier.get("value"):
            return str(identifier["value"])
    return None
