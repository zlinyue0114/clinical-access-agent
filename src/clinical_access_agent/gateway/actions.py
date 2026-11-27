"""Confirmation-bound actions (invariant I3).

The only consequential action is adding a report to the clinician's follow-up
worklist. It takes two steps:

1. ``prepare`` (agent or ui token) validates access and returns a confirmation:
   a random id plus the SHA-256 digest of the exact action, valid for a few minutes.
2. ``commit`` (ui token only) must present the same id and digest. The confirmation
   is consumed on the first attempt, whether or not that attempt succeeds.

The worklist lives in the gateway, not in the FHIR server; the record store is never
written to.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from clinical_access_agent.gateway.errors import GatewayError, Reason

ACTION_ADD_FOLLOW_UP = "follow_up.add"


@dataclass(frozen=True)
class PendingAction:
    confirmation_id: str
    digest: str
    action: str
    clinician_id: str
    patient_id: str
    report_id: str
    interval_months: int
    expires_at: datetime


def _invalid() -> GatewayError:
    return GatewayError(409, Reason.CONFIRMATION_INVALID)


class ConfirmationStore:
    def __init__(self, *, ttl: timedelta, now: Callable[[], datetime]) -> None:
        self._ttl = ttl
        self._now = now
        self._pending: dict[str, PendingAction] = {}
        self._lock = threading.Lock()

    def create(
        self, *, clinician_id: str, patient_id: str, report_id: str, interval_months: int
    ) -> PendingAction:
        confirmation_id = secrets.token_hex(16)
        expires_at = self._now() + self._ttl
        payload = {
            "action": ACTION_ADD_FOLLOW_UP,
            "confirmation_id": confirmation_id,
            "clinician_id": clinician_id,
            "patient_id": patient_id,
            "report_id": report_id,
            "interval_months": interval_months,
            "expires_at": expires_at.isoformat(),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        pending = PendingAction(
            confirmation_id=confirmation_id,
            digest=hashlib.sha256(canonical).hexdigest(),
            action=ACTION_ADD_FOLLOW_UP,
            clinician_id=clinician_id,
            patient_id=patient_id,
            report_id=report_id,
            interval_months=interval_months,
            expires_at=expires_at,
        )
        with self._lock:
            self._drop_expired()
            self._pending[confirmation_id] = pending
        return pending

    def consume(self, *, confirmation_id: str, digest: str, clinician_id: str) -> PendingAction:
        with self._lock:
            pending = self._pending.pop(confirmation_id, None)
        if pending is None:
            raise _invalid()
        if pending.clinician_id != clinician_id:
            raise _invalid()
        if not hmac.compare_digest(pending.digest, digest):
            raise _invalid()
        if self._now() >= pending.expires_at:
            raise _invalid()
        return pending

    def _drop_expired(self) -> None:
        now = self._now()
        for key in [k for k, v in self._pending.items() if now >= v.expires_at]:
            del self._pending[key]


@dataclass(frozen=True)
class WorklistEntry:
    entry_id: str
    clinician_id: str
    patient_id: str
    report_id: str
    interval_months: int
    confirmation_digest: str
    created_at: str


class Worklist:
    """Append-only JSONL store of confirmed follow-up entries."""

    def __init__(self, path: Path, *, now: Callable[[], datetime]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._now = now
        self._lock = threading.Lock()

    def add(self, pending: PendingAction) -> WorklistEntry:
        entry = WorklistEntry(
            entry_id=uuid.uuid4().hex,
            clinician_id=pending.clinician_id,
            patient_id=pending.patient_id,
            report_id=pending.report_id,
            interval_months=pending.interval_months,
            confirmation_digest=pending.digest,
            created_at=self._now().isoformat(timespec="seconds"),
        )
        line = json.dumps(asdict(entry), sort_keys=True).encode() + b"\n"
        with self._lock, self._path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def entries(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        with self._path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
