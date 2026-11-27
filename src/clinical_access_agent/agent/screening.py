"""Screening of record text for embedded instructions (invariant I5).

This is a heuristic, defense-in-depth layer. It is not the security boundary: the
gateway denies out-of-panel reads and exposes no write or export endpoint whether or
not screening catches an injection. Screening exists to keep instructions out of
the model's context and to flag reports for human review, and its recall and false
positive rate are measured separately in the evaluation (M4), including on
phrasings that are not in the generation corpus.

Each sentence is checked against five categories, in English and German. A flagged
sentence is withheld from the model and replaced by a marker that names the
category but not the content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Category(StrEnum):
    ADDRESSED_TO_MODEL = "addressed_to_model"
    OTHER_PATIENT = "other_patient_reference"
    DATA_REQUEST = "bulk_data_request"
    ACTION_REQUEST = "action_request"
    FOLLOW_UP_OVERRIDE = "follow_up_override"


_I = re.IGNORECASE

_ADDRESSED = [
    re.compile(
        r"\b(note|instruction|message|hinweis|anweisung)\s+(to|for|an|f(ü|ue)r)\s+"
        r"(the\s+|das\s+|die\s+|den\s+)?(ai|a\.i\.|assistant|model|system|ki|"
        r"assistenten|modell|chatbot|llm)\b",
        _I,
    ),
    re.compile(r"\b(ai|ki|llm|assistant)\s+(instruction|note|anweisung|hinweis)\b", _I),
    re.compile(r"\bsystem\s*(note|override|prompt|message|instruction)\b", _I),
    re.compile(r"\bsystem(hinweis|anweisung|nachricht)\b", _I),
    re.compile(
        r"\b(ignore|disregard|forget|override)\b.{0,30}\b(instructions?|rules?|prompt|"
        r"guidelines?|guidance|directions?|constraints?)\b",
        _I,
    ),
    re.compile(r"^\W*(assistant|ai|a\.i\.|model|system|chatbot|ki|assistent)\s*[,:]", _I),
    re.compile(
        r"\b(ignorier\w*|vergiss|missacht\w*)\b.{0,40}\b(anweisung\w*|regeln?|"
        r"vorgaben?)\b",
        _I,
    ),
    re.compile(r"\b(you are now|du bist jetzt|act as|agiere als)\b", _I),
]

_IDENTIFIER = re.compile(r"\b[A-Za-z]{2,6}-\d{3,}\b")
_OTHER_PATIENT = [
    # The gateway replaces identifiers it does not recognize as the patient's own
    # with this placeholder; their presence points at another person's record.
    re.compile(r"\[ID\]"),
    re.compile(r"\b(another|other|different|second)\s+patient", _I),
    re.compile(r"\b(anderen?|weiteren?|zweiten?)\s+patient", _I),
    re.compile(r"\bpatient(in|en)?\s+(no\.?\s*|nr\.?\s*|#\s*)?\d{2,}\b", _I),
]

_BULK_OBJECT = re.compile(
    r"\b(all|every|complete\s+list|full\s+list|alle[nmrs]?|s(ä|ae)mtliche[nmrs]?|"
    r"vollst(ä|ae)ndige?)\b.{0,40}\b(patients?|patient\s+data|records?|identifiers?|"
    r"patient(en|in)?(daten)?|akten|befunde)\b"
    r"|\b(dates?\s+of\s+birth|insurance\s+numbers?|full\s+names|geburtsdat\w*|"
    r"patient\s+lists?|patientenliste\w*|"
    r"versicherungsnummer\w*|identifiers)\b",
    _I,
)
_BULK_VERB = re.compile(
    r"\b(list|export|send|e-?mail|mail|attach|include|forward|retrieve|summari[sz]e|share|upload|"
    r"exportier\w*|sende\w*|schick\w*|anh(ä|ae)ng\w*|auflist\w*|abruf\w*|rufe|"
    r"weiterleit\w*|teil\w*)\b",
    _I,
)

_ACTION_VERB = re.compile(
    r"\b(mark|close|cancel|delete|remove|modify|update|change|sign|approve|order|schedule|"
    r"markier\w*|schlie(ß|ss)\w*|stornier\w*|l(ö|oe)sch\w*|entfern\w*|(ä|ae)nder\w*|"
    r"freigeb\w*|abschlie(ß|ss)\w*|absag\w*)\b",
    _I,
)
_ACTION_OBJECT = re.compile(
    r"\b(reports?|orders?|follow-?ups?|appointments?|records?|status|reviewed|worklist|"
    r"befund\w*|auftr(ä|ae)ge?|auftrag|termin\w*|kontroll\w*|verlaufskontrolle|"
    r"arbeitsliste|im\s+system|in\s+the\s+system)\b",
    _I,
)

_OVERRIDE = [
    re.compile(
        r"\bno\s+(further\s+|additional\s+)?(follow-?up|surveillance|imaging|"
        r"control)\s+(is\s+|are\s+)?(required|needed|necessary|indicated)\b",
        _I,
    ),
    re.compile(
        r"\bkeine\s+(weitere\s+)?(kontrolle|verlaufskontrolle|nachsorge|"
        r"bildgebung|nachkontrolle)\s+(mehr\s+)?(n(ö|oe)tig|erforderlich|notwendig|"
        r"indiziert)\b",
        _I,
    ),
    re.compile(r"\b(requiring|requires?)\s+no\s+follow-?up\b", _I),
    re.compile(r"\b(already|was|been|previously)\s+(resected|removed|excised|treated)\b", _I),
    re.compile(r"\b(bereits|schon)\s+(reseziert|entfernt|exzidiert|behandelt|operiert)\b", _I),
    re.compile(r"\bregardless\s+of\s+(the\s+)?findings\b|\bunabh(ä|ae)ngig\s+vom\s+befund\b", _I),
]

_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class Flag:
    category: Category
    sentence_index: int


@dataclass(frozen=True)
class ScreenResult:
    text: str
    flags: list[Flag] = field(default_factory=list)

    @property
    def categories(self) -> list[str]:
        return sorted({f.category.value for f in self.flags})


def classify(sentence: str) -> Category | None:
    """Return the first matching category, or ``None`` for an ordinary sentence."""
    if any(p.search(sentence) for p in _ADDRESSED):
        return Category.ADDRESSED_TO_MODEL
    if _IDENTIFIER.search(sentence) or any(p.search(sentence) for p in _OTHER_PATIENT):
        return Category.OTHER_PATIENT
    if _BULK_OBJECT.search(sentence) and _BULK_VERB.search(sentence):
        return Category.DATA_REQUEST
    if any(p.search(sentence) for p in _OVERRIDE):
        return Category.FOLLOW_UP_OVERRIDE
    if _ACTION_VERB.search(sentence) and _ACTION_OBJECT.search(sentence):
        return Category.ACTION_REQUEST
    return None


def screen(text: str) -> ScreenResult:
    """Withhold flagged sentences; keep line structure of the rest."""
    flags: list[Flag] = []
    out_lines: list[str] = []
    index = 0
    for line in text.split("\n"):
        kept: list[str] = []
        for sentence in (s for s in _SPLIT.split(line) if s.strip()):
            category = classify(sentence)
            if category is None:
                kept.append(sentence)
            else:
                flags.append(Flag(category, index))
                kept.append(f"[sentence withheld by screening: {category.value}]")
            index += 1
        out_lines.append(" ".join(kept))
    return ScreenResult(text="\n".join(out_lines), flags=flags)
