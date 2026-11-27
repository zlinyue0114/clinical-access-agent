"""Error types and the reason codes recorded in the audit log."""

from __future__ import annotations

from enum import StrEnum


class Reason(StrEnum):
    """Why a request was allowed or denied. Stored in every audit record."""

    OK = "ok"
    INVALID_TOKEN = "invalid_token"  # noqa: S105  (reason code, not a secret)
    WRONG_AUDIENCE = "wrong_audience"
    NOT_FOUND = "not_found"
    QUOTA_EXCEEDED = "quota_exceeded"
    CONFIRMATION_INVALID = "confirmation_invalid"
    BAD_REQUEST = "bad_request"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    INTERNAL_ERROR = "internal_error"


class GatewayError(Exception):
    """A request the gateway refuses. The client only sees ``reason``."""

    def __init__(self, status: int, reason: Reason) -> None:
        super().__init__(reason.value)
        self.status = status
        self.reason = reason


def not_found() -> GatewayError:
    """Unknown and unauthorized resources are indistinguishable to the client."""
    return GatewayError(404, Reason.NOT_FOUND)
