"""Deterministic follow-up rules used to compute ground truth.

These rules are simplified for a software demonstration and are NOT clinical
guidance. They loosely follow the 2017 Fleischner Society recommendations for a
single incidental solid pulmonary nodule in adults aged 35 or older, and common
practice for incidental breast findings on chest CT.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FollowUp(StrEnum):
    """Whether a finding calls for follow-up."""

    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class Risk(StrEnum):
    """Patient risk category for lung cancer (Fleischner terminology)."""

    LOW = "low"
    HIGH = "high"


class BreastFindingType(StrEnum):
    """Incidental breast findings on chest CT."""

    BENIGN_CALCIFIED = "benign_calcified"
    CIRCUMSCRIBED_MASS = "circumscribed_mass"
    SUSPICIOUS_MASS = "suspicious_mass"


@dataclass(frozen=True)
class Recommendation:
    """The outcome of a rule: urgency, suggested interval and next examination."""

    follow_up: FollowUp
    interval_months: int | None
    next_exam: str | None


def solid_nodule_recommendation(diameter_mm: float, risk: Risk) -> Recommendation:
    """Simplified recommendation for a single solid pulmonary nodule.

    - below 6 mm: no routine follow-up (low risk); optional CT at 12 months (high risk)
    - 6 to 8 mm: CT at 6 to 12 months (here: 6)
    - above 8 mm: CT at 3 months, PET/CT or tissue sampling (here: CT at 3 months)
    """
    if diameter_mm <= 0:
        raise ValueError("diameter_mm must be positive")
    if diameter_mm < 6:
        if risk is Risk.LOW:
            return Recommendation(FollowUp.NONE, None, None)
        return Recommendation(FollowUp.OPTIONAL, 12, "chest CT")
    if diameter_mm <= 8:
        return Recommendation(FollowUp.REQUIRED, 6, "chest CT")
    return Recommendation(FollowUp.REQUIRED, 3, "chest CT")


def breast_finding_recommendation(finding: BreastFindingType) -> Recommendation:
    """Simplified recommendation for an incidental breast finding on chest CT."""
    if finding is BreastFindingType.BENIGN_CALCIFIED:
        return Recommendation(FollowUp.NONE, None, None)
    return Recommendation(FollowUp.REQUIRED, 0, "diagnostic mammography and ultrasound")


def combine(recommendations: list[Recommendation]) -> FollowUp:
    """The most urgent follow-up level across all findings of one report."""
    order = [FollowUp.NONE, FollowUp.OPTIONAL, FollowUp.REQUIRED]
    level = FollowUp.NONE
    for rec in recommendations:
        if order.index(rec.follow_up) > order.index(level):
            level = rec.follow_up
    return level
