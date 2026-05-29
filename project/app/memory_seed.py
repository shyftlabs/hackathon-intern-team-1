"""Seed long-term (USER-scope) memory with customer history for the demo.

The Fraud and Marketing agents search long-term memory before reasoning, so we
pre-load each demo customer's fraud history, LTV tier, and channel preferences
as durable facts. This is what lets the Fraud agent recognise ``cust_frank`` as
a serial returner even on a brand-new return, and lets Marketing tailor the
incentive to ``cust_alpha``'s platinum status.

mem0 fact-extraction runs through the Smart Gateway at the ``auto/cheap`` tier
(the only tier compatible with mem0's forced-tool-call schema).
"""

from __future__ import annotations

from app import seed_data
from app.config import APP_CONFIG


async def seed_customer_memories(memory_client, *, only: list[str] | None = None) -> int:
    """Write each customer's history as USER-scoped memories.

    Returns the number of customers seeded. No-op (returns 0) if the memory
    client is disabled — the engine stays fully functional without memory.
    """
    if memory_client is None or not getattr(memory_client, "is_enabled", False):
        return 0

    targets = only or list(seed_data.CUSTOMERS.keys())
    seeded = 0
    for customer_id in targets:
        c = seed_data.CUSTOMERS.get(customer_id)
        if not c:
            continue
        fraud_line = (
            f"has {c['fraud_flags']} prior fraud flags"
            if c["fraud_flags"]
            else "has no prior fraud flags (clean history)"
        )
        facts = [
            {
                "role": "user",
                "content": (
                    f"Customer {c['name']} ({customer_id}) is a {c['ltv_tier']}-tier customer "
                    f"with lifetime value ${c['lifetime_value']:.0f}. They have made "
                    f"{c['return_count']} returns total and {c['past_exchanges']} past exchanges, "
                    f"and {fraud_line}. Preferred contact channel: {c['preferred_channel']}. "
                    f"Account notes: {c['notes']}"
                ),
            },
            {
                "role": "assistant",
                "content": (
                    f"Recorded customer profile for {customer_id}: tier={c['ltv_tier']}, "
                    f"fraud_flags={c['fraud_flags']}, preferred_channel={c['preferred_channel']}."
                ),
            },
        ]
        try:
            await memory_client.add(facts, user_id=customer_id, metadata={"category": "crm_profile"})
            seeded += 1
        except Exception as exc:  # best-effort seeding; never block startup
            from orchestrator import get_logger

            get_logger(__name__).warning(f"memory seed failed for {customer_id}: {exc}")
    return seeded


async def maybe_seed(memory_client) -> int:
    """Seed only when configured to use memory and the client is live."""
    if not APP_CONFIG.enable_memory:
        return 0
    return await seed_customer_memories(memory_client)
