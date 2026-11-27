"""Gateway settings, read from environment variables.

=============================  ==============================================
``CAA_FHIR_BASE``              FHIR base URL (default ``http://127.0.0.1:8090/fhir``)
``CAA_TOKEN_PUBLIC_KEY_FILE``  issuer public key, PEM (default ``.secrets/issuer_ed25519.pub.pem``)
``CAA_TOKEN_ISSUER``           expected ``iss`` claim
``CAA_PSEUDONYM_KEY_FILE``     hex-encoded pseudonym key (default ``.secrets/pseudonym.key``)
``CAA_AUDIT_PATH``             audit log (default ``audit/gateway.jsonl``)
``CAA_WORKLIST_PATH``          follow-up worklist (default ``audit/worklist.jsonl``)
``CAA_MAX_REQUESTS_PER_MINUTE``  default 300
``CAA_MAX_PATIENTS_PER_HOUR``    default 60
=============================  ==============================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from clinical_access_agent.gateway.tokens import DEFAULT_ISSUER

MRN_SYSTEM = "urn:clinical-access-agent:mrn"


@dataclass(frozen=True)
class Settings:
    fhir_base: str
    token_public_key: bytes
    pseudonym_key: bytes
    audit_path: Path
    worklist_path: Path
    token_issuer: str = DEFAULT_ISSUER
    max_requests_per_minute: int = 300
    max_patients_per_hour: int = 60
    confirmation_ttl: timedelta = timedelta(minutes=5)
    fhir_timeout_seconds: float = 5.0
    # Evaluation only: lets the test suite measure the agent-side layers with
    # identifying text present. Not configurable from the environment.
    deidentify_text: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        key_hex = Path(env.get("CAA_PSEUDONYM_KEY_FILE", ".secrets/pseudonym.key")).read_text()
        return cls(
            fhir_base=env.get("CAA_FHIR_BASE", "http://127.0.0.1:8090/fhir"),
            token_public_key=Path(
                env.get("CAA_TOKEN_PUBLIC_KEY_FILE", ".secrets/issuer_ed25519.pub.pem")
            ).read_bytes(),
            pseudonym_key=bytes.fromhex(key_hex.strip()),
            audit_path=Path(env.get("CAA_AUDIT_PATH", "audit/gateway.jsonl")),
            worklist_path=Path(env.get("CAA_WORKLIST_PATH", "audit/worklist.jsonl")),
            token_issuer=env.get("CAA_TOKEN_ISSUER", DEFAULT_ISSUER),
            max_requests_per_minute=int(env.get("CAA_MAX_REQUESTS_PER_MINUTE", "300")),
            max_patients_per_hour=int(env.get("CAA_MAX_PATIENTS_PER_HOUR", "60")),
        )
