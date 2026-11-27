"""Report text templates in English and German.

Several phrasings per finding keep the task from being solvable by keyword
matching. Impressions state the recommendation only some of the time, so the
reader has to reason from the findings as well.
"""

from __future__ import annotations

import random

from clinical_access_agent.data.rules import BreastFindingType, Recommendation

LOBES = {
    "en": [
        "right upper lobe",
        "right middle lobe",
        "right lower lobe",
        "left upper lobe",
        "left lower lobe",
    ],
    "de": [
        "rechten Oberlappen",
        "rechten Mittellappen",
        "rechten Unterlappen",
        "linken Oberlappen",
        "linken Unterlappen",
    ],
}

SIDES = {"en": ["right", "left"], "de": ["rechts", "links"]}

NO_NODULE = {
    "en": [
        "No pulmonary nodules or masses.",
        "Lungs are clear without focal lesion.",
        "No suspicious pulmonary lesion identified.",
    ],
    "de": [
        "Keine pulmonalen Rundherde.",
        "Lunge ohne fokale Läsion.",
        "Kein suspekter pulmonaler Herd abgrenzbar.",
    ],
}

NODULE = {
    "en": [
        "Solid nodule in the {lobe} measuring {size} mm.",
        "A {size} mm solid pulmonary nodule is seen in the {lobe}.",
        "{lobe_cap}: solid nodule, mean diameter {size} mm.",
    ],
    "de": [
        "Solider Rundherd im {lobe} mit {size} mm Durchmesser.",
        "Im {lobe} solider Knoten von {size} mm.",
        "Solider pulmonaler Nodulus im {lobe}, mittlerer Durchmesser {size} mm.",
    ],
}

BREAST = {
    "en": {
        BreastFindingType.BENIGN_CALCIFIED: [
            "Coarsely calcified lesion in the {side} breast, consistent with a benign "
            "involuting fibroadenoma.",
        ],
        BreastFindingType.CIRCUMSCRIBED_MASS: [
            "Incidental circumscribed soft-tissue mass in the {side} breast, {size} mm, "
            "no prior imaging available.",
        ],
        BreastFindingType.SUSPICIOUS_MASS: [
            "Irregular spiculated mass in the {side} breast measuring {size} mm.",
        ],
    },
    "de": {
        BreastFindingType.BENIGN_CALCIFIED: [
            "Grobschollig verkalkte Läsion der Mamma {side}, vereinbar mit einem "
            "regressiven Fibroadenom.",
        ],
        BreastFindingType.CIRCUMSCRIBED_MASS: [
            "Zufällig abgebildete, glatt begrenzte Raumforderung der Mamma {side}, "
            "{size} mm, keine Voraufnahmen.",
        ],
        BreastFindingType.SUSPICIOUS_MASS: [
            "Unregelmäßig begrenzte, spikulierte Raumforderung der Mamma {side}, {size} mm.",
        ],
    },
}

BACKGROUND = {
    "en": [
        "No pleural effusion. Heart size normal. No mediastinal lymphadenopathy.",
        "Mild emphysema. No consolidation. No effusion.",
        "Mediastinum unremarkable. No pleural or pericardial effusion.",
    ],
    "de": [
        "Kein Pleuraerguss. Herz normal groß. Keine mediastinale Lymphadenopathie.",
        "Geringes Lungenemphysem. Keine Infiltrate. Kein Erguss.",
        "Mediastinum unauffällig. Kein Pleura- oder Perikarderguss.",
    ],
}

HEADERS = {
    "en": ("CT THORAX", "Findings", "Impression"),
    "de": ("CT THORAX", "Befund", "Beurteilung"),
}

UNREMARKABLE_IMPRESSION = {
    "en": "No acute findings.",
    "de": "Kein akuter Befund.",
}


def recommendation_sentence(rec: Recommendation, language: str) -> str:
    """Human-readable recommendation, used in some impressions."""
    if rec.next_exam is None:
        return ""
    if rec.interval_months == 0:
        return (
            "Recommend diagnostic mammography and ultrasound."
            if language == "en"
            else "Empfehlung: diagnostische Mammographie und Sonographie."
        )
    if language == "en":
        return f"Follow-up chest CT in {rec.interval_months} months recommended."
    return f"Verlaufskontrolle mittels CT Thorax in {rec.interval_months} Monaten empfohlen."


def nodule_sentence(rng: random.Random, language: str, lobe_index: int, size: float) -> str:
    lobe = LOBES[language][lobe_index]
    template = rng.choice(NODULE[language])
    return template.format(lobe=lobe, lobe_cap=lobe[:1].upper() + lobe[1:], size=f"{size:g}")


def breast_sentence(
    rng: random.Random, language: str, kind: BreastFindingType, side_index: int, size: int
) -> str:
    template = rng.choice(BREAST[language][kind])
    return template.format(side=SIDES[language][side_index], size=size)
