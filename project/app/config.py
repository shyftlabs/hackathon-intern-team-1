"""Application configuration.

Pure-stdlib (no ``orchestrator`` import) so it is safe to import anywhere. Reads
toggles from the environment (already populated by ``app.__init__``).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app import PROJECT_ROOT


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class AppConfig:
    """Runtime configuration for the returns engine."""

    # --- Smart Gateway --------------------------------------------------------
    # When SMART_GATEWAY_URL is set, every agent's ``model`` is rewritten to
    # ``auto/<tier>`` by the gateway provider; ``gateway_mode`` selects the tier.
    gateway_url: str | None = field(default_factory=lambda: os.environ.get("SMART_GATEWAY_URL"))

    # Placeholder model — the gateway translates it to auto/<tier> at runtime.
    # (A model string containing "/" or starting with "auto" passes through
    # unchanged, so keep this slash-free to let gateway_mode drive the tier.)
    base_model: str = field(default_factory=lambda: os.environ.get("RETURNS_BASE_MODEL", "gpt-4o-mini"))

    # The Fraud + Synthesis agents need a vision/quality model. Slash-free →
    # becomes auto/quality. Override with e.g. "openai/gpt-4o" to force a
    # specific vision model to pass through the gateway unchanged.
    vision_model: str = field(default_factory=lambda: os.environ.get("RETURNS_VISION_MODEL", "gpt-4o"))

    # --- Subsystems -----------------------------------------------------------
    enable_memory: bool = field(default_factory=lambda: _flag("MEMORY_ENABLED", True))
    enable_session: bool = field(default_factory=lambda: _flag("SESSION_ENABLED", True))
    enable_langfuse: bool = field(default_factory=lambda: _flag("LANGFUSE_ENABLED", True))
    # Use the IntelligentMemoryClient (importance scoring + time decay) instead
    # of the plain MemoryClient. Drop-in; part of the "stale fraud signals get
    # down-weighted" pitch.
    use_intelligent_memory: bool = field(default_factory=lambda: _flag("USE_INTELLIGENT_MEMORY", True))

    # --- Runtime --------------------------------------------------------------
    max_turns: int = field(default_factory=lambda: int(os.environ.get("RETURNS_MAX_TURNS", "8")))
    reroute_radius_km: int = field(default_factory=lambda: int(os.environ.get("RETURNS_RADIUS_KM", "150")))
    # Fraud risk at/above this triggers the human-in-the-loop approval gate.
    fraud_review_threshold: float = field(
        default_factory=lambda: float(os.environ.get("RETURNS_FRAUD_THRESHOLD", "0.6"))
    )

    # --- Observability --------------------------------------------------------
    langfuse_host: str = field(
        default_factory=lambda: os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    )

    @property
    def gateway_active(self) -> bool:
        return bool(self.gateway_url)

    def trace_url(self, trace_id: str | None) -> str | None:
        if not trace_id:
            return None
        return f"{self.langfuse_host.rstrip('/')}/trace/{trace_id}"


APP_CONFIG = AppConfig()

# Absolute path to the venv python (the one running this process). The MCP
# server is spawned as a subprocess with this interpreter so it can import
# ``mcp`` (FastMCP).
VENV_PYTHON = sys.executable
APP_DIR = Path(__file__).resolve().parent
MCP_SERVER_SCRIPT = str(APP_DIR / "mcp_server.py")

__all__ = ["AppConfig", "APP_CONFIG", "PROJECT_ROOT", "VENV_PYTHON", "MCP_SERVER_SCRIPT", "APP_DIR"]
