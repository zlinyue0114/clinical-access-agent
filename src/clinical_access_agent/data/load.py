"""Post a generated transaction bundle to a FHIR server.

Usage::

    python -m clinical_access_agent.data.load --bundle data/generated/bundle.json \
        --fhir http://127.0.0.1:8090/fhir
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def load(bundle_path: Path, fhir_base: str, timeout: float = 120.0) -> int:
    """Send the bundle as one transaction. Returns the number of entries processed."""
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    response = httpx.post(
        fhir_base.rstrip("/"),
        json=bundle,
        headers={"Content-Type": "application/fhir+json"},
        timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()
    return len(result.get("entry", []))


def main() -> None:
    parser = argparse.ArgumentParser(description="Load a FHIR transaction bundle.")
    parser.add_argument("--bundle", type=Path, default=Path("data/generated/bundle.json"))
    parser.add_argument("--fhir", default="http://127.0.0.1:8090/fhir")
    args = parser.parse_args()
    print(f"processed {load(args.bundle, args.fhir)} entries")


if __name__ == "__main__":
    main()
