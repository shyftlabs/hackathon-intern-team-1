"""
Automated Omnichannel Returns Optimization & Reallocation Engine.

Built on the ShyftLabs Continuum agentic framework (import root: ``orchestrator``).

IMPORTANT — environment bootstrap:
    The orchestrator ``settings`` object is a module-level singleton that caches
    every value on first import. So the project ``.env`` MUST be loaded into the
    process environment *before* anything imports ``orchestrator``.

    Python guarantees a package's ``__init__`` runs before any of its submodules'
    bodies execute. Every module in this app that touches ``orchestrator`` lives
    under the ``app`` package, so loading the env here makes the ordering correct
    no matter which submodule is imported first.
"""

from __future__ import annotations

import os
from pathlib import Path

# Project root = parent of this ``app`` package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = PROJECT_ROOT / ".env"


def _bootstrap_env() -> None:
    """Load ``project/.env`` and clear stale Smart-Gateway vars not in the file.

    Mirrors the playground ``config.py`` convention: ``override=True`` so the
    .env wins over stale shell exports, and any ``SMART_GATEWAY_*`` that is *not*
    declared in the file is removed from ``os.environ`` so a previous shell can
    never accidentally re-activate the gateway.
    """
    try:
        from dotenv import dotenv_values, load_dotenv
    except Exception:  # pragma: no cover - dotenv is a Continuum dependency
        # Minimal fallback parser if python-dotenv is somehow unavailable.
        if _ENV_PATH.exists():
            for line in _ENV_PATH.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.split("#")[0].strip())
        return

    if not _ENV_PATH.exists():
        return
    load_dotenv(_ENV_PATH, override=True)
    file_env = dotenv_values(_ENV_PATH)
    for var in ("SMART_GATEWAY_URL", "SMART_GATEWAY_API_KEY"):
        if var not in file_env:
            os.environ.pop(var, None)


_bootstrap_env()
