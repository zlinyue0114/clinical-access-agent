"""Run the gateway locally: ``python -m clinical_access_agent.gateway``.

Binds to 127.0.0.1 only. Settings come from the environment, see ``config.py``.
"""

from __future__ import annotations

import os

import uvicorn

from clinical_access_agent.gateway.app import create_app
from clinical_access_agent.gateway.config import Settings


def main() -> None:
    app = create_app(Settings.from_env())
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("CAA_GATEWAY_PORT", "8100")))


if __name__ == "__main__":
    main()
