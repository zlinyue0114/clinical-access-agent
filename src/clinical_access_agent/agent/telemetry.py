"""Telemetry settings (invariant I8).

ADK can attach prompts, tool arguments and tool results to trace spans. Those would
contain record text, so content capture is switched off before an agent is built.
Spans keep timing, tool names and token counts.
"""

from __future__ import annotations

import os

CONTENT_CAPTURE_VARIABLES = (
    "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
)


def disable_content_capture() -> None:
    for name in CONTENT_CAPTURE_VARIABLES:
        os.environ[name] = "false"
