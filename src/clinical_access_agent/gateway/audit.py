"""Hash-chained, append-only audit log (invariant I6).

Every request the gateway receives produces exactly one record, whether it was
allowed or denied. Each record stores the hash of the previous record, so editing,
deleting or reordering any record breaks the chain. Truncating the end of the log
cannot be detected from the file alone; compare the head hash with a value kept
elsewhere (``caa-audit verify --expect-head``).

Records never contain report text or patient names.

Usage::

    caa-audit verify audit/gateway.jsonl
    caa-audit head audit/gateway.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
FIELDS = (
    "seq",
    "ts",
    "request_id",
    "clinician",
    "audience",
    "method",
    "route",
    "action",
    "resource",
    "patient",
    "status",
    "decision",
    "reason",
    "prev",
)
MAX_RESOURCE_CHARS = 128


class AuditIntegrityError(Exception):
    """The existing log does not verify; the gateway refuses to start on it."""


@dataclass(frozen=True)
class Verification:
    ok: bool
    records: int
    head: str
    error: str | None = None
    line: int | None = None


def _canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def record_hash(record: dict[str, Any]) -> str:
    body = {k: record[k] for k in FIELDS}
    return hashlib.sha256(_canonical(body)).hexdigest()


def verify(path: Path) -> Verification:
    """Check sequence numbers, previous-hash links and record hashes."""
    if not path.exists():
        return Verification(ok=True, records=0, head=GENESIS)
    prev, count = GENESIS, 0
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return Verification(False, count, prev, "malformed record", number)
            if not isinstance(record, dict) or set(record) != {*FIELDS, "hash"}:
                return Verification(False, count, prev, "unexpected fields", number)
            if record["seq"] != count:
                return Verification(False, count, prev, "sequence gap", number)
            if record["prev"] != prev:
                return Verification(False, count, prev, "broken chain", number)
            if record["hash"] != record_hash(record):
                return Verification(False, count, prev, "hash mismatch", number)
            prev, count = record["hash"], count + 1
    return Verification(ok=True, records=count, head=prev)


class AuditLog:
    def __init__(self, path: Path, *, now: Callable[[], datetime] | None = None) -> None:
        state = verify(path)
        if not state.ok:
            raise AuditIntegrityError(f"{path}: {state.error} at line {state.line}")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._now = now or (lambda: datetime.now(UTC))
        self._seq = state.records
        self._head = state.head
        self._lock = threading.Lock()

    @property
    def head(self) -> str:
        return self._head

    def append(
        self,
        *,
        request_id: str,
        clinician: str | None,
        audience: str | None,
        method: str,
        route: str,
        action: str,
        resource: str | None,
        patient: str | None,
        status: int,
        reason: str,
    ) -> dict[str, Any]:
        with self._lock:
            record: dict[str, Any] = {
                "seq": self._seq,
                "ts": self._now().isoformat(timespec="milliseconds"),
                "request_id": request_id,
                "clinician": clinician,
                "audience": audience,
                "method": method,
                "route": route,
                "action": action,
                "resource": resource[:MAX_RESOURCE_CHARS] if resource else None,
                "patient": patient,
                "status": status,
                "decision": "allow" if status < 400 else "deny",
                "reason": reason,
                "prev": self._head,
            }
            record["hash"] = record_hash(record)
            with self._path.open("ab") as handle:
                handle.write(_canonical(record) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._seq += 1
            self._head = record["hash"]
            return record


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="caa-audit", description="Verify a gateway audit log.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verify", help="verify the hash chain")
    check.add_argument("path", type=Path)
    check.add_argument("--expect-head", help="head hash recorded elsewhere, detects truncation")
    head = sub.add_parser("head", help="print the current head hash")
    head.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    result = verify(args.path)
    if args.command == "head":
        print(result.head)
        sys.exit(0 if result.ok else 1)
    if not result.ok:
        print(f"FAIL {args.path}: {result.error} at line {result.line}")
        sys.exit(1)
    if args.expect_head and args.expect_head != result.head:
        print(f"FAIL {args.path}: head does not match the expected value")
        sys.exit(1)
    print(f"OK {args.path}: {result.records} records, head {result.head}")


if __name__ == "__main__":
    main()
