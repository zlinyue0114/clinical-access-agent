"""The repository must not contain Chinese text (project rule)."""

import re
from pathlib import Path

_CJK_RANGES = [
    (0x3000, 0x303F),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
]
CJK = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _CJK_RANGES) + "]")
ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", ".git", "data", ".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__"}
SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".cfg", ".example", ""}


def test_no_cjk_in_tracked_text_files() -> None:
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or set(path.relative_to(ROOT).parts) & SKIP:
            continue
        if path.suffix not in SUFFIXES or path.name == "uv.lock":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if CJK.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
