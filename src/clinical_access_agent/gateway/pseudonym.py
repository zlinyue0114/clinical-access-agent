"""Keyed pseudonyms for patients and reports.

A pseudonym is ``HMAC-SHA256(key, kind | clinician | resource id)``, truncated to
80 bits. Including the clinician makes pseudonyms unlinkable across clinicians: the
same patient has a different pseudonym for every clinician who can see them.

Pseudonyms are not reversible. The gateway resolves one by recomputing the
pseudonyms of the resources the clinician may access and looking it up, so
resolution doubles as the authorization check: a reference outside the panel simply
does not resolve.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable
from enum import StrEnum

MIN_KEY_BYTES = 32
_DIGEST_HEX = 20


class Kind(StrEnum):
    PATIENT = "p"
    REPORT = "r"


_FORMAT = {kind: re.compile(rf"^{kind.value}_[0-9a-f]{{{_DIGEST_HEX}}}$") for kind in Kind}


class PseudonymCollisionError(Exception):
    """Two resources in one lookup map to the same pseudonym."""


class Pseudonymizer:
    def __init__(self, key: bytes) -> None:
        if len(key) < MIN_KEY_BYTES:
            raise ValueError(f"pseudonym key must be at least {MIN_KEY_BYTES} bytes")
        self._key = key

    def pseudonym(self, clinician_id: str, kind: Kind, resource_id: str) -> str:
        message = f"{kind.value}|{clinician_id}|{resource_id}".encode()
        digest = hmac.new(self._key, message, hashlib.sha256).hexdigest()
        return f"{kind.value}_{digest[:_DIGEST_HEX]}"

    def lookup(self, clinician_id: str, kind: Kind, resource_ids: Iterable[str]) -> dict[str, str]:
        """Map pseudonym to resource id for the given resources."""
        table: dict[str, str] = {}
        for resource_id in resource_ids:
            alias = self.pseudonym(clinician_id, kind, resource_id)
            if alias in table and table[alias] != resource_id:
                raise PseudonymCollisionError(alias)
            table[alias] = resource_id
        return table

    @staticmethod
    def is_well_formed(value: str, kind: Kind) -> bool:
        return bool(_FORMAT[kind].match(value))
