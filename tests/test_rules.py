import pytest

from clinical_access_agent.data.rules import (
    BreastFindingType,
    FollowUp,
    Recommendation,
    Risk,
    breast_finding_recommendation,
    combine,
    solid_nodule_recommendation,
)


@pytest.mark.parametrize(
    ("size", "risk", "expected", "interval"),
    [
        (3, Risk.LOW, FollowUp.NONE, None),
        (5.9, Risk.LOW, FollowUp.NONE, None),
        (5, Risk.HIGH, FollowUp.OPTIONAL, 12),
        (6, Risk.LOW, FollowUp.REQUIRED, 6),
        (8, Risk.HIGH, FollowUp.REQUIRED, 6),
        (8.1, Risk.LOW, FollowUp.REQUIRED, 3),
        (18, Risk.HIGH, FollowUp.REQUIRED, 3),
    ],
)
def test_solid_nodule_thresholds(
    size: float, risk: Risk, expected: FollowUp, interval: int | None
) -> None:
    rec = solid_nodule_recommendation(size, risk)
    assert rec.follow_up is expected
    assert rec.interval_months == interval


def test_solid_nodule_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError):
        solid_nodule_recommendation(0, Risk.LOW)


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        (BreastFindingType.BENIGN_CALCIFIED, FollowUp.NONE),
        (BreastFindingType.CIRCUMSCRIBED_MASS, FollowUp.REQUIRED),
        (BreastFindingType.SUSPICIOUS_MASS, FollowUp.REQUIRED),
    ],
)
def test_breast_findings(finding: BreastFindingType, expected: FollowUp) -> None:
    assert breast_finding_recommendation(finding).follow_up is expected


def test_combine_takes_most_urgent() -> None:
    none = Recommendation(FollowUp.NONE, None, None)
    optional = Recommendation(FollowUp.OPTIONAL, 12, "chest CT")
    required = Recommendation(FollowUp.REQUIRED, 3, "chest CT")
    assert combine([]) is FollowUp.NONE
    assert combine([none, optional]) is FollowUp.OPTIONAL
    assert combine([optional, required, none]) is FollowUp.REQUIRED
