"""De-identification of free text before it leaves the gateway (invariant I4).

Structured fields are minimized by the response models. Free text needs its own
step, because real reports carry identifying headers and mention patients by name.

Two passes:

1. **Known identifiers.** The gateway holds the patient resource, so it removes that
   patient's own name parts, record numbers and date of birth (in the usual
   layouts) exactly.
2. **Identifier shapes.** Anything that still looks like a record identifier
   (``MRN-123456``, ``pat-0042``) or a labelled date of birth is removed, which also
   strips identifiers of *other* patients planted in the text.

Placeholders name the kind of value, so the text stays readable: ``[NAME]``,
``[DOB]``, ``[MRN]`` and ``[PATIENT_ID]`` for the patient's own data, ``[ID]`` for an
identifier the gateway does not recognize as the patient's own. Re-identification
is only possible through the ``/identity`` endpoint with a ui token.

Limitation: names that the gateway does not know (relatives, other people) are only
caught after a title such as *Mr* or *Frau*. A production deployment should add a
dedicated de-identification service (for example Cloud DLP) behind this step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

MONTHS = {
    "en": [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ],
    "de": [
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ],
}  # fmt: skip

_IDENTIFIER_SHAPE = re.compile(r"\b[A-Za-z]{2,6}-\d{3,}\b")
_LABELLED_DATE = re.compile(
    r"\b(DOB|D\.O\.B\.|date of birth|born|geb\.|geboren|Geburtsdatum)\s*:?\s*"
    r"(\d{1,4}[./-]\d{1,2}[./-]\d{1,4})",
    re.IGNORECASE,
)
_TITLED_NAME = re.compile(
    r"\b(Mr|Mrs|Ms|Miss|Dr|Herr|Herrn|Frau)(\.?\s+)[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?\b"
)


@dataclass(frozen=True)
class Identity:
    names: tuple[str, ...]
    birth_date: date | None
    identifiers: tuple[tuple[str, str], ...]  # (value, placeholder)


def identity_of(patient: dict[str, Any], mrn_system: str) -> Identity:
    names: set[str] = set()
    for name in patient.get("name", []):
        for part in [*name.get("given", []), name.get("family", "")]:
            names.update(p for p in re.split(r"[\s-]+", str(part)) if len(p) >= 2)
            if part:
                names.add(str(part))
    identifiers = tuple(
        (str(i["value"]), "[MRN]" if i.get("system") == mrn_system else "[PATIENT_ID]")
        for i in patient.get("identifier", [])
        if i.get("value")
    )
    born: date | None = None
    if isinstance(patient.get("birthDate"), str):
        try:
            born = date.fromisoformat(patient["birthDate"][:10])
        except ValueError:
            born = None
    return Identity(
        names=tuple(sorted(names, key=len, reverse=True)),
        birth_date=born,
        identifiers=identifiers,
    )


def _date_variants(born: date) -> list[str]:
    d, m, y = born.day, born.month, born.year
    variants = {
        born.isoformat(),
        f"{d:02d}.{m:02d}.{y}", f"{d}.{m}.{y}",
        f"{d:02d}/{m:02d}/{y}", f"{m:02d}/{d:02d}/{y}", f"{d}/{m}/{y}",
        f"{d:02d}-{m:02d}-{y}",
        f"{d} {MONTHS['en'][m - 1]} {y}", f"{MONTHS['en'][m - 1]} {d}, {y}",
        f"{d}. {MONTHS['de'][m - 1]} {y}",
    }  # fmt: skip
    return sorted(variants, key=len, reverse=True)


def deidentify(text: str, identity: Identity) -> tuple[str, int]:
    """Return the de-identified text and the number of replacements."""
    count = 0

    def sub(pattern: re.Pattern[str], placeholder: str, value: str) -> str:
        nonlocal count
        value, n = pattern.subn(placeholder, value)
        count += n
        return value

    for value, placeholder in identity.identifiers:
        text = sub(re.compile(re.escape(value), re.IGNORECASE), placeholder, text)
    if identity.birth_date is not None:
        for variant in _date_variants(identity.birth_date):
            text = sub(re.compile(re.escape(variant)), "[DOB]", text)
    for name in identity.names:
        text = sub(re.compile(rf"(?<!\w){re.escape(name)}(?!\w)"), "[NAME]", text)

    text = sub(_IDENTIFIER_SHAPE, "[ID]", text)
    text = sub(_LABELLED_DATE, r"\1: [DOB]", text)
    text = sub(_TITLED_NAME, r"\1\2[NAME]", text)
    return text, count
