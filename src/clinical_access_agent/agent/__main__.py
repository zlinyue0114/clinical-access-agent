"""Ask the agent a question from the command line.

Requires a running gateway (``make gateway``) and, for a real model, Vertex AI
credentials (see ``.env.example``)::

    caa-ask --clinician prac-01 "Which of my patients' chest CT reports need follow-up?"
    caa-ask --clinician prac-01 --model scripted "..."   # offline, no model calls

``--clinician`` issues a short-lived agent token with the development issuer key.
In a deployment the token comes from the clinician's signed-in session instead.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

import httpx

from clinical_access_agent.agent.agent import ask, default_model
from clinical_access_agent.agent.client import GatewayClient
from clinical_access_agent.agent.stub import ScriptedModel
from clinical_access_agent.gateway.tokens import Audience, issue


async def _run(args: argparse.Namespace) -> None:
    token = issue(args.key.read_bytes(), args.clinician, Audience.AGENT)
    model = ScriptedModel() if args.model == "scripted" else args.model
    async with httpx.AsyncClient(timeout=30) as http:
        client = GatewayClient(args.gateway, token, http)
        result = await ask(args.question, client, model=model)
    print(result.answer)
    if args.trace:
        trace = {
            "status": result.status,
            "llm_calls": result.llm_calls,
            "tool_calls": result.tool_calls,
            "events": [asdict(e) for e in result.events if e.kind != "tool_allowed"],
        }
        print(json.dumps(trace, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="caa-ask", description="Ask the follow-up agent.")
    parser.add_argument("question")
    parser.add_argument("--clinician", required=True)
    parser.add_argument("--model", default=default_model(), help="model name or 'scripted'")
    parser.add_argument(
        "--gateway", default=os.environ.get("CAA_GATEWAY_URL", "http://127.0.0.1:8100")
    )
    parser.add_argument("--key", type=Path, default=Path(".secrets/issuer_ed25519.pem"))
    parser.add_argument("--trace", action="store_true", help="print guard decisions")
    asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
