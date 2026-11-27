"""Agent-side redaction of identifying text in tool results.

The gateway de-identifies report text before it leaves the security boundary. This
module is an independent second pass, applied in ``after_tool`` before a result
enters the model context: if the gateway step is misconfigured or misses a layout,
labelled identifiers, record-number shapes, labelled dates of birth and titled
names are still removed.

It works on patterns only, because the agent does not know who the patient is.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[REDACTED]"

_NAME = r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?"
_DATE = r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}"

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[A-Za-z]{2,6}-\d{3,}\b"), PLACEHOLDER),
    (
        re.compile(
            rf"\b(DOB|born|geb\.|geboren|date of birth|Geburtsdatum)(\s*:?\s*){_DATE}",
            re.IGNORECASE,
        ),
        rf"\1\2{PLACEHOLDER}",
    ),
    (re.compile(rf"\b(Mr|Ms|Mrs|Miss|Herr|Herrn|Frau)(\.?\s+){_NAME}"), rf"\1\2{PLACEHOLDER}"),
    (
        re.compile(
            r"\b(Patient\(in\)|Patientin|Patient|Name)(\s*:\s*)(?![\s\[])[^|\n]*?(?=\s{2,}|\s*\||$)",
            re.MULTILINE,
        ),
        rf"\1\2{PLACEHOLDER}",
    ),
]


def redact(text: str) -> tuple[str, int]:
    """Return the text with identifying spans replaced, and the number replaced."""
    count = 0
    for pattern, replacement in _RULES:
        text, n = pattern.subn(replacement, text)
        count += n
    return text, count
