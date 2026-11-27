"""Session tokens: Ed25519-signed JWTs that carry the clinician identity.

The gateway holds only the public key. The private key belongs to the issuer, which
in this repository is a development command-line tool::

    caa-token keygen --dir .secrets
    caa-token issue --key .secrets/issuer_ed25519.pem --clinician prac-01 --aud agent

In a deployment the issuer would be the hospital identity provider; the gateway
code does not change.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_ISSUER = "clinical-access-agent-dev-issuer"
DEFAULT_TTL = timedelta(minutes=15)
MAX_TTL = timedelta(hours=8)
_ALGORITHM = "EdDSA"
_SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,63}$")


class Audience(StrEnum):
    """Who presents the token.

    ``agent`` tokens are handed to the agent runtime and allow read access only.
    ``ui`` tokens belong to the clinician's own interface and additionally allow
    re-identification and confirming a prepared action.
    """

    AGENT = "agent"
    UI = "ui"


class TokenError(Exception):
    """The token is missing, malformed, expired or not signed by the issuer."""


@dataclass(frozen=True)
class Principal:
    clinician_id: str
    audience: Audience
    token_id: str


def generate_keypair() -> tuple[bytes, bytes]:
    """Return ``(private_pem, public_pem)`` for a new Ed25519 key."""
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


def issue(
    private_pem: bytes,
    clinician_id: str,
    audience: Audience,
    *,
    issuer: str = DEFAULT_ISSUER,
    ttl: timedelta = DEFAULT_TTL,
    now: datetime | None = None,
) -> str:
    """Sign a token for ``clinician_id``."""
    if not _SUBJECT.match(clinician_id):
        raise ValueError("invalid clinician id")
    if not timedelta(0) < ttl <= MAX_TTL:
        raise ValueError("ttl out of range")
    issued = now or datetime.now(UTC)
    claims = {
        "iss": issuer,
        "sub": clinician_id,
        "aud": audience.value,
        "iat": int(issued.timestamp()),
        "exp": int((issued + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    key = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("issuer key must be Ed25519")
    return jwt.encode(claims, key, algorithm=_ALGORITHM)


def verify(token: str, public_pem: bytes, *, issuer: str = DEFAULT_ISSUER) -> Principal:
    """Verify signature, issuer, audience and lifetime. Raise ``TokenError`` otherwise."""
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != _ALGORITHM:
            raise TokenError("unexpected algorithm")
        claims = jwt.decode(
            token,
            serialization.load_pem_public_key(public_pem),  # type: ignore[arg-type]
            algorithms=[_ALGORITHM],
            audience=[a.value for a in Audience],
            issuer=issuer,
            options={"require": ["iss", "sub", "aud", "iat", "exp", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(type(exc).__name__) from exc

    audience, subject, token_id = claims["aud"], claims["sub"], claims["jti"]
    if not isinstance(audience, str):
        raise TokenError("audience must be a single value")
    if not isinstance(subject, str) or not _SUBJECT.match(subject):
        raise TokenError("invalid subject")
    if not isinstance(token_id, str) or not token_id:
        raise TokenError("invalid token id")
    if claims["exp"] - claims["iat"] > MAX_TTL.total_seconds():
        raise TokenError("lifetime too long")
    return Principal(clinician_id=subject, audience=Audience(audience), token_id=token_id)


_KEY_FILES = ("issuer_ed25519.pem", "issuer_ed25519.pub.pem", "pseudonym.key")


def _write_secret(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="caa-token", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="create the issuer key pair and pseudonym key")
    keygen.add_argument("--dir", type=Path, default=Path(".secrets"))

    issue_cmd = sub.add_parser("issue", help="print a signed token")
    issue_cmd.add_argument("--key", type=Path, default=Path(".secrets/issuer_ed25519.pem"))
    issue_cmd.add_argument("--clinician", required=True)
    issue_cmd.add_argument("--aud", choices=[a.value for a in Audience], default="agent")
    issue_cmd.add_argument("--ttl-minutes", type=int, default=15)
    issue_cmd.add_argument("--issuer", default=DEFAULT_ISSUER)

    args = parser.parse_args(argv)
    if args.command == "keygen":
        existing = [p for p in _KEY_FILES if (args.dir / p).exists()]
        if existing:
            parser.exit(1, f"refusing to overwrite existing keys in {args.dir}/: {existing}\n")
        private_pem, public_pem = generate_keypair()
        _write_secret(args.dir / "issuer_ed25519.pem", private_pem)
        (args.dir / "issuer_ed25519.pub.pem").write_bytes(public_pem)
        _write_secret(args.dir / "pseudonym.key", secrets.token_hex(32).encode("ascii"))
        print(f"wrote keys to {args.dir}/")
    else:
        token = issue(
            args.key.read_bytes(),
            args.clinician,
            Audience(args.aud),
            issuer=args.issuer,
            ttl=timedelta(minutes=args.ttl_minutes),
        )
        print(token)


if __name__ == "__main__":
    main()
