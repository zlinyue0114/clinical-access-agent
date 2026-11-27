"""Per-clinician quotas (invariant I7).

Two sliding windows per clinician:

- requests per minute, counting every authenticated request;
- distinct patients per hour, counting each patient once per window.

The distinct-patient limit is what bounds the damage of a compromised agent session:
it can re-read the same few patients, but it cannot walk the whole panel.

State is held in process memory, so limits apply per gateway instance.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from clinical_access_agent.gateway.errors import GatewayError, Reason

_MINUTE = 60.0
_HOUR = 3600.0


class Quota:
    def __init__(
        self,
        *,
        max_requests_per_minute: int,
        max_patients_per_hour: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_requests = max_requests_per_minute
        self._max_patients = max_patients_per_hour
        self._clock = clock
        self._lock = threading.Lock()
        self._requests: defaultdict[str, deque[float]] = defaultdict(deque)
        self._patients: defaultdict[str, dict[str, float]] = defaultdict(dict)

    def check_request(self, clinician_id: str) -> None:
        now = self._clock()
        with self._lock:
            window = self._requests[clinician_id]
            while window and now - window[0] >= _MINUTE:
                window.popleft()
            if len(window) >= self._max_requests:
                raise GatewayError(429, Reason.QUOTA_EXCEEDED)
            window.append(now)

    def check_patient(self, clinician_id: str, patient_id: str) -> None:
        now = self._clock()
        with self._lock:
            seen = self._patients[clinician_id]
            for pid in [p for p, t in seen.items() if now - t >= _HOUR]:
                del seen[pid]
            if patient_id in seen:
                return
            if len(seen) >= self._max_patients:
                raise GatewayError(429, Reason.QUOTA_EXCEEDED)
            seen[patient_id] = now
