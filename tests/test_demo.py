from __future__ import annotations

import asyncio

import pytest

from clinical_access_agent.demo import main_async


def test_demo_runs_and_reports_a_held_boundary(capsys: pytest.CaptureFixture[str]) -> None:
    asyncio.run(main_async())
    out = capsys.readouterr().out
    assert "Flagged for human review: True" in out
    assert "all on the panel: True" in out
    assert "hash chain intact: True" in out
    assert "MRN-" not in out.split("3. ")[0]
