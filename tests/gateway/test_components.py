"""Unit tests for tokens, pseudonyms, the audit chain and the FHIR client."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest

from clinical_access_agent.gateway import audit
from clinical_access_agent.gateway.audit import AuditIntegrityError, AuditLog
from clinical_access_agent.gateway.fhir import MAX_PAGES, FhirClient, FhirUnavailableError
from clinical_access_agent.gateway.pseudonym import Kind, Pseudonymizer
from clinical_access_agent.gateway.tokens import (
    Audience,
    TokenError,
    issue,
    verify,
)

# Tokens -----------------------------------------------------------------------------


def test_token_round_trip(keys: tuple[bytes, bytes]) -> None:
    token = issue(keys[0], "prac-01", Audience.UI)
    who = verify(token, keys[1])
    assert (who.clinician_id, who.audience) == ("prac-01", Audience.UI)


def test_expired_token_is_rejected(keys: tuple[bytes, bytes]) -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    token = issue(keys[0], "prac-01", Audience.AGENT, now=past, ttl=timedelta(minutes=5))
    with pytest.raises(TokenError):
        verify(token, keys[1])


def test_wrong_issuer_is_rejected(keys: tuple[bytes, bytes]) -> None:
    token = issue(keys[0], "prac-01", Audience.AGENT, issuer="someone-else")
    with pytest.raises(TokenError):
        verify(token, keys[1])


def test_unsigned_and_symmetric_tokens_are_rejected(keys: tuple[bytes, bytes]) -> None:
    now = int(datetime.now(UTC).timestamp())
    claims: dict[str, Any] = {
        "iss": "clinical-access-agent-dev-issuer",
        "sub": "prac-01",
        "aud": "agent",
        "iat": now,
        "exp": now + 600,
        "jti": "x",
    }
    unsigned = jwt.encode(claims, key="", algorithm="none")
    # Algorithm confusion: an HMAC token keyed with the public key.
    header = jwt.utils.base64url_encode(b'{"alg":"HS256","typ":"JWT"}')
    body = jwt.utils.base64url_encode(json.dumps(claims).encode())
    mac = hmac.new(keys[1], header + b"." + body, hashlib.sha256).digest()
    symmetric = b".".join([header, body, jwt.utils.base64url_encode(mac)]).decode()
    for token in (unsigned, symmetric):
        with pytest.raises(TokenError):
            verify(token, keys[1])


def test_tampered_claims_are_rejected(keys: tuple[bytes, bytes]) -> None:
    header, payload, signature = issue(keys[0], "prac-01", Audience.AGENT).split(".")
    claims = json.loads(jwt.utils.base64url_decode(payload))
    claims["sub"] = "prac-02"
    forged = jwt.utils.base64url_encode(json.dumps(claims).encode()).decode()
    with pytest.raises(TokenError):
        verify(f"{header}.{forged}.{signature}", keys[1])


def test_unknown_or_multiple_audiences_are_rejected(keys: tuple[bytes, bytes]) -> None:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private = load_pem_private_key(keys[0], password=None)
    now = int(datetime.now(UTC).timestamp())
    base = {
        "iss": "clinical-access-agent-dev-issuer",
        "sub": "prac-01",
        "iat": now,
        "exp": now + 600,
        "jti": "x",
    }
    for audience in ("admin", ["agent", "ui"]):
        token = jwt.encode({**base, "aud": audience}, private, algorithm="EdDSA")  # type: ignore[arg-type]
        with pytest.raises(TokenError):
            verify(token, keys[1])


def test_issue_validates_input(keys: tuple[bytes, bytes]) -> None:
    with pytest.raises(ValueError):
        issue(keys[0], "../admin", Audience.AGENT)
    with pytest.raises(ValueError):
        issue(keys[0], "prac-01", Audience.AGENT, ttl=timedelta(days=2))


# Pseudonyms -------------------------------------------------------------------------


def test_pseudonyms_are_stable_keyed_and_per_clinician() -> None:
    a = Pseudonymizer(b"a" * 32)
    b = Pseudonymizer(b"b" * 32)
    assert a.pseudonym("c1", Kind.PATIENT, "pat-1") == a.pseudonym("c1", Kind.PATIENT, "pat-1")
    assert a.pseudonym("c1", Kind.PATIENT, "pat-1") != a.pseudonym("c2", Kind.PATIENT, "pat-1")
    assert a.pseudonym("c1", Kind.PATIENT, "pat-1") != b.pseudonym("c1", Kind.PATIENT, "pat-1")
    assert a.pseudonym("c1", Kind.PATIENT, "x") != a.pseudonym("c1", Kind.REPORT, "x")
    alias = a.pseudonym("c1", Kind.REPORT, "rep-1")
    assert Pseudonymizer.is_well_formed(alias, Kind.REPORT)
    assert not Pseudonymizer.is_well_formed(alias, Kind.PATIENT)
    assert "rep-1" not in alias


def test_short_pseudonym_keys_are_refused() -> None:
    with pytest.raises(ValueError):
        Pseudonymizer(b"short")


# Audit chain ------------------------------------------------------------------------


def _write(log: AuditLog, n: int) -> None:
    for i in range(n):
        log.append(
            request_id=str(i),
            clinician="prac-01",
            audience="agent",
            method="GET",
            route="/v1/patients",
            action="patients.list",
            resource=None,
            patient=None,
            status=200,
            reason="ok",
        )


def test_chain_verifies_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    _write(AuditLog(path), 3)
    log = AuditLog(path)
    _write(log, 2)
    result = audit.verify(path)
    assert result.ok and result.records == 5 and result.head == log.head


@pytest.mark.parametrize("tamper", ["edit", "delete", "swap", "garbage"])
def test_any_modification_breaks_the_chain(tmp_path: Path, tamper: str) -> None:
    path = tmp_path / "audit.jsonl"
    _write(AuditLog(path), 4)
    lines = path.read_text().splitlines()
    if tamper == "edit":
        lines[1] = lines[1].replace('"status":200', '"status":201')
    elif tamper == "delete":
        del lines[1]
    elif tamper == "swap":
        lines[1], lines[2] = lines[2], lines[1]
    else:
        lines.insert(2, "{not json")
    path.write_text("\n".join(lines) + "\n")
    assert not audit.verify(path).ok
    with pytest.raises(AuditIntegrityError):
        AuditLog(path)


def test_truncation_is_caught_with_an_external_head(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    _write(log, 3)
    head = log.head
    path.write_text("\n".join(path.read_text().splitlines()[:2]) + "\n")
    assert audit.verify(path).ok
    with pytest.raises(SystemExit) as exit_info:
        audit.main(["verify", str(path), "--expect-head", head])
    assert exit_info.value.code == 1


# FHIR client ------------------------------------------------------------------------


def _bundle(resources: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "resourceType": "Bundle",
        "entry": [{"resource": r} for r in resources],
    }
    if next_url:
        body["link"] = [{"relation": "next", "url": next_url}]
    return body


async def test_paging_follows_next_links_on_our_own_base() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "_getpages" in request.url.query.decode():
            return httpx.Response(200, json=_bundle([{"resourceType": "Patient", "id": "b"}]))
        return httpx.Response(
            200,
            json=_bundle(
                [{"resourceType": "Patient", "id": "a"}, {"resourceType": "OperationOutcome"}],
                next_url="http://internal-host:8080/fhir?_getpages=abc&_getpagesoffset=1",
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        found = await FhirClient("http://fhir.test/fhir", http).search("Patient", {"_id": "a,b"})
    assert [r["id"] for r in found] == ["a", "b"]
    assert all(url.startswith("http://fhir.test/fhir") for url in seen)


async def test_endless_paging_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bundle([], next_url="http://x/fhir?_getpages=1"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(FhirUnavailableError):
            await FhirClient("http://fhir.test/fhir", http).search("Patient", {"_id": "a"})


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"resourceType": "OperationOutcome"}),
    ],
)
async def test_bad_upstream_answers_fail_closed(response: httpx.Response) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as http:
        with pytest.raises(FhirUnavailableError):
            await FhirClient("http://fhir.test/fhir", http).search("CareTeam", {"participant": "x"})


async def test_read_rejects_mismatched_resources_and_odd_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"resourceType": "Patient", "id": "someone-else"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = FhirClient("http://fhir.test/fhir", http)
        with pytest.raises(FhirUnavailableError):
            await client.read("Patient", "pat-1")
        assert await client.read("Patient", "../Practitioner/x") is None


async def test_transport_errors_fail_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(FhirUnavailableError):
            await FhirClient("http://fhir.test/fhir", http).read("Patient", "pat-1")


def test_page_limit_is_finite() -> None:
    assert 0 < MAX_PAGES <= 100
