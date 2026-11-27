"""Documentation stays consistent with the repository."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "DESIGN_SPEC.md", *sorted((ROOT / "docs").rglob("*.md"))]
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def _test_names() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "tests").rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        }
    return names


def test_relative_links_resolve() -> None:
    broken = []
    for doc in DOCS:
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("mailto:"):
                continue
            if not (doc.parent / target).resolve().exists():
                broken.append(f"{doc.relative_to(ROOT)}: {target}")
    assert not broken


def test_tests_cited_as_evidence_exist() -> None:
    known = _test_names()
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        cited = set(re.findall(r"`(test_\w+)`", text))
        assert cited <= known, (doc.name, sorted(cited - known))
        for path in re.findall(r"`(tests/[\w/]+\.py)`", text):
            assert (ROOT / path).exists(), (doc.name, path)
