#!/usr/bin/env python3
"""OPTIONAL: durable fraud-review approval gate on Temporal.

The core engine (app/engine.py) implements the human-in-the-loop gate in-process
(POST /returns/{id}/approve) — reliable and dependency-free for the demo. This
module shows the *durable* alternative: when a return is flagged for fraud, run
a Temporal workflow `agent -> approval -> agent` that survives process crashes
and parks (potentially for days) until a human signals a decision.

Requires a live Temporal server (docker compose brings one up on :7233). Run:

    ../continuum/.venv/bin/python temporal_workflow.py

It will: start a worker, launch a flagged-return workflow, poll until it parks
on the approval, submit an approval signal, and print the resumed result.

API verified against continuum/playground/temporal_e2e_test.py.
"""

from __future__ import annotations

import asyncio
import os
import sys

import app  # noqa: F401  -- .env bootstrap before orchestrator import

# Temporal must be enabled for the client to connect (overrides .env default).
os.environ.setdefault("TEMPORAL_ENABLED", "true")

from orchestrator.agent import BaseAgent  # noqa: E402
from orchestrator.agent.config import AgentConfig, AgentMemoryConfig  # noqa: E402

from app.config import APP_CONFIG  # noqa: E402

# The Temporal SDK (temporalio) is an optional extra. The server runs on :7233,
# but the Python client must be installed separately. Degrade gracefully so this
# module never crashes the project if it isn't present.
try:
    from orchestrator.temporal import (  # noqa: E402
        ApprovalDecision,
        WorkflowInput,
        get_agent_registry,
        get_temporal_client,
        get_worker_manager,
    )

    TEMPORAL_IMPORT_ERROR: str | None = None
except Exception as _exc:  # ImportError when temporalio isn't installed
    TEMPORAL_IMPORT_ERROR = str(_exc)


_INSTALL_HINT = (
    "Temporal support needs the temporalio SDK, which isn't installed in this venv.\n"
    "  Install it:  ../continuum/.venv/bin/pip install temporalio\n"
    "  (the Temporal server itself is already running on :7233 via docker compose)\n"
    "The core engine's in-process approval gate (POST /returns/{id}/approve) works "
    "without Temporal — this module is the durable, crash-safe alternative."
)

_NO_MEM = AgentMemoryConfig(search_memories=False, store_memories=False)


def make_fraud_reviewer() -> BaseAgent:
    """The agent that runs on both sides of the durable approval gate."""
    return BaseAgent(
        name="fraud-reviewer",
        model=APP_CONFIG.base_model,
        gateway_mode="modest",
        instructions=(
            "You are a fraud-ops reviewer. Before approval: write a 2-sentence brief for the "
            "human reviewer on what to verify. After approval: confirm the resolution (refund "
            "released or item rerouted) in one sentence."
        ),
        memory_config=_NO_MEM,
        config=AgentConfig(log_to_session=False),
    )


async def run_durable_fraud_review(
    return_summary: str, *, workflow_id: str, approver: str = "ops-team", auto_approve: bool = True
) -> dict:
    """Run the durable agent -> approval -> agent workflow and return the result.

    If ``auto_approve`` is True the function self-approves after the workflow
    parks (for a hands-free demo). In a real deployment the approval signal comes
    from your ops UI calling ``client.signal_workflow(..., 'submit_approval', ...)``.
    """
    if TEMPORAL_IMPORT_ERROR:
        return {"status": "temporal_unavailable", "detail": TEMPORAL_IMPORT_ERROR}
    registry = get_agent_registry()
    registry.register(make_fraud_reviewer())

    client = get_temporal_client()
    await client.connect()
    worker = get_worker_manager(client, registry)
    await worker.start()

    try:
        handle = await client.run_agent_workflow(
            WorkflowInput(
                steps=[
                    {"type": "agent", "agent_name": "fraud-reviewer"},
                    {
                        "type": "approval",
                        "description": f"Fraud-flagged return needs review: {return_summary}",
                        "approvers": [approver],
                    },
                    {"type": "agent", "agent_name": "fraud-reviewer"},
                ],
                initial_input=return_summary,
            ),
            id=workflow_id,
        )

        # Poll until the workflow parks on the approval gate.
        request_id = None
        for _ in range(30):
            await asyncio.sleep(1)
            pending = await client.query_workflow(workflow_id, "get_pending_approvals")
            if pending:
                request_id = pending[0]["request_id"]
                break

        if request_id is None:
            await client.cancel_workflow(workflow_id)
            return {"status": "no_pending_approval"}

        print(f"[temporal] workflow parked on approval request_id={request_id}")

        if not auto_approve:
            return {"status": "waiting_for_approval", "workflow_id": workflow_id, "request_id": request_id}

        await client.signal_workflow(
            workflow_id,
            "submit_approval",
            ApprovalDecision(request_id=request_id, decision="approved", decided_by=approver),
        )
        result = await handle.result()
        return {"status": result.status, "content": result.content}
    finally:
        await worker.stop()


async def main() -> int:
    if TEMPORAL_IMPORT_ERROR:
        print("=== Durable fraud-review approval gate (Temporal) ===\n")
        print(_INSTALL_HINT)
        return 2
    summary = (
        "Return RMA-7731: cust_frank (serial returner, 2 prior wardrobing flags) claims "
        "$119.99 earbuds 'defective', photo inconsistent with claim. Fraud risk 0.85."
    )
    print("=== Durable fraud-review approval gate (Temporal) ===")
    print(f"  flagged return: {summary}\n")
    out = await run_durable_fraud_review(summary, workflow_id=f"fraud-review-{os.getpid()}")
    print(f"\n  workflow status: {out.get('status')}")
    if out.get("content"):
        print(f"  resolution: {out['content']}")
    ok = out.get("status") == "completed"
    print("\n" + ("✅ DURABLE APPROVAL GATE PASSED" if ok else f"⚠️  result: {out}"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
