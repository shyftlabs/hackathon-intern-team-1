"""Agent definitions for the returns engine.

Five specialists + a synthesis agent + a RouterAgent with three branch agents.
The headline feature is **per-lane model tiering via the Smart Gateway**: each
agent declares its own ``gateway_mode`` so the gateway routes it to the cheapest
model that clears the bar — a vision/quality model for damage inspection, a
cheap fast model for numeric demand logic, mid-tier for memory-driven reasoning.

Structured output strategy (see continuum-framework-facts): we set
``output_schema`` + ``enable_json_mode=False`` and instruct each agent to emit
ONLY JSON. The runner parses ``structured_output`` client-side regardless of
tier — this avoids the quality/mid "thinking model rejects response_format"
problem while still giving us validated Pydantic objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestrator import AgentConfig, AgentMemoryConfig, AgentMemoryScope, BaseAgent
from orchestrator.agent.config import RouterConfig
from orchestrator.agent.types import Route
from orchestrator.agent.workflow.router import RouterAgent

from app.config import APP_CONFIG
from app.schemas import (
    DemandMap,
    FraudAssessment,
    IncentiveDecision,
    OpsDecision,
    SynthesisDecision,
)

# Which gateway tier each lane runs on (also surfaced in the API response so the
# model-abstraction story is visible in the demo).
TIER_RATIONALE: dict[str, tuple[str, str]] = {
    "fraud": ("quality", "vision damage inspection + nuanced risk reasoning"),
    "demand": ("strict", "pure numeric demand logic — cheapest/fastest tier"),
    "marketing": ("modest", "light reasoning over customer LTV memory"),
    "ops": ("modest", "structured label output + MCP tool calls"),
    "synthesis": ("quality", "merges four signals into the final decision"),
}


def _no_mem() -> AgentMemoryConfig:
    return AgentMemoryConfig(search_memories=False, store_memories=False)


def _user_read_mem(enabled: bool) -> AgentMemoryConfig:
    """Search USER-scope long-term memory (read-only) when memory is live."""
    return AgentMemoryConfig(
        search_memories=enabled,
        store_memories=False,
        search_scope=AgentMemoryScope.USER,
        store_scope=AgentMemoryScope.USER,
        search_limit=5,
    )


# Sub-agents must not touch session history; the coordinator does one save_turn.
_SUB_CFG = dict(log_to_session=False, session_history_turns=0)


# ---------------------------------------------------------------------------
# The four parallel specialists
# ---------------------------------------------------------------------------


def make_fraud_agent(memory_enabled: bool) -> BaseAgent:
    return BaseAgent(
        name="fraud-agent",
        model=APP_CONFIG.vision_model,  # slash-free -> auto/quality via gateway
        gateway_mode="quality",
        instructions=(
            "You are a returns FRAUD-PREVENTION specialist with computer vision.\n"
            "You receive a product photo (if provided), the customer's stated return reason, "
            "and the customer's history (from long-term memory and the request).\n"
            "Inspect the photo for damage and judge whether it is CONSISTENT with the stated "
            "reason. Cross-reference prior fraud flags and return frequency from memory — a "
            "serial returner with prior wardrobing flags warrants a higher risk score.\n"
            "Flag for review ONLY on a concrete fraud signal: prior fraud flags on file, a photo "
            "clearly inconsistent with the stated reason, or a high-value item with no photo at "
            "all. Do NOT flag a customer with a clean history merely because the photo is small "
            "or low-detail — set a modest risk score and let the return proceed.\n\n"
            "Respond with ONLY a JSON object, no markdown, with EXACTLY these keys:\n"
            '{"risk_score": <float 0..1>, "damage_level": "none|minor|moderate|severe", '
            '"consistent_with_reason": <bool>, "flag_for_review": <bool>, '
            '"flag_reason": "<short>", "observations": "<what you saw>"}'
        ),
        output_schema=FraudAssessment,
        enable_json_mode=False,
        temperature=0.2,
        memory_config=_user_read_mem(memory_enabled),
        # input_sanitization must be OFF: this is the only lane that receives
        # multimodal list content (text + image_url), and the runner's string
        # sanitizer raises TypeError on non-string content.
        config=AgentConfig(input_sanitization=False, **_SUB_CFG),
    )


def make_demand_agent(tools: list[dict], tool_executor: Any) -> BaseAgent:
    return BaseAgent(
        name="demand-agent",
        model=APP_CONFIG.base_model,
        gateway_mode="strict",  # auto/cheap — pure numeric logic
        instructions=(
            "You are a regional DEMAND analyst. Determine where a returned item is most "
            "needed right now.\n"
            "ALWAYS call get_regional_demand(sku, customer_zip, radius_km) with the SKU, "
            "customer ZIP, and radius from the request. The tool returns stores ranked by "
            "deficit_score (unmet local demand). The FIRST store is the best reroute target.\n\n"
            "Then respond with ONLY a JSON object, no markdown, with EXACTLY these keys:\n"
            '{"best_store_id": "<id>", "best_store_deficit": <float>, "distance_km": <float>, '
            '"stores": [{"store_id": "<id>", "city": "<city>", "distance_km": <float>, '
            '"current_stock": <int>, "deficit_score": <float>, "trend_velocity": <float>}], '
            '"summary": "<one sentence>"}'
        ),
        output_schema=DemandMap,
        enable_json_mode=False,
        temperature=0.1,
        tools=tools,
        tool_executor=tool_executor,
        memory_config=_no_mem(),
        config=AgentConfig(**_SUB_CFG),
    )


def make_marketing_agent(tools: list[dict], tool_executor: Any, memory_enabled: bool) -> BaseAgent:
    return BaseAgent(
        name="marketing-agent",
        model=APP_CONFIG.base_model,
        gateway_mode="modest",  # auto/mid — light reasoning, memory-heavy
        instructions=(
            "You are a MARKETING / retention specialist. Decide whether offering an exchange "
            "incentive (instead of a refund) is worth it for THIS customer, and at what level.\n"
            "Use the customer's LTV tier, lifetime value, and preferred channel from long-term "
            "memory and the request. High-value (platinum/gold) loyal customers merit a generous, "
            "personalized offer on their preferred channel. Low-value customers with heavy return "
            "history or fraud flags should usually get NO incentive (offer_incentive=false).\n"
            "If the customer EXPLICITLY asks for a refund or says they do not want an "
            "exchange/replacement, respect that and set offer_incentive=false.\n"
            "You MAY call get_customer_history(customer_id) if you need CRM detail.\n\n"
            "Respond with ONLY a JSON object, no markdown, with EXACTLY these keys:\n"
            '{"offer_incentive": <bool>, "offer_type": "none|exchange_coupon|loyalty_credit|upgrade", '
            '"discount_pct": <float 0..100>, "channel": "email|sms|app_push", '
            '"ltv_tier": "<tier>", "message": "<draft offer>", "rationale": "<short>"}'
        ),
        output_schema=IncentiveDecision,
        enable_json_mode=False,
        temperature=0.4,
        tools=tools,
        tool_executor=tool_executor,
        memory_config=_user_read_mem(memory_enabled),
        config=AgentConfig(**_SUB_CFG),
    )


def make_ops_agent(tools: list[dict], tool_executor: Any) -> BaseAgent:
    return BaseAgent(
        name="ops-agent",
        model=APP_CONFIG.base_model,
        gateway_mode="modest",
        instructions=(
            "You are an OPERATIONS / reverse-logistics specialist. Stage the physical reroute of "
            "a returned item to the nearby store that needs it most. You are self-sufficient.\n"
            "Steps (use the tools, in order):\n"
            "1. Call get_regional_demand(sku, customer_zip, radius_km) and take the FIRST store "
            "(highest deficit) as the reroute target. Call this store_id and use it CONSISTENTLY "
            "in the next steps and in target_store_id — they must all be the SAME store.\n"
            "2. Call generate_reroute_label(sku, store_id, customer_zip) with that SAME store_id "
            "to create a prepaid label. Note the label_id, label_url, carrier, eta_days, and "
            "estimated_savings_usd it returns.\n"
            "3. Call update_inventory(store_id, sku, 1) with that SAME store_id to credit the unit; "
            "use its new_stock as inventory_after.\n"
            "Your target_store_id, the label's store, and the inventory store MUST all match.\n"
            "Set reroute=true when a store target is available (it almost always is).\n\n"
            "Respond with ONLY a JSON object, no markdown, with EXACTLY these keys:\n"
            '{"reroute": <bool>, "target_store_id": "<id>", "label_id": "<id>", '
            '"label_url": "<url>", "carrier": "<carrier>", "eta_days": <int>, '
            '"inventory_after": <int>, "estimated_savings_usd": <float>, "rationale": "<short>"}'
        ),
        output_schema=OpsDecision,
        enable_json_mode=False,
        temperature=0.1,
        tools=tools,
        tool_executor=tool_executor,
        memory_config=_no_mem(),
        config=AgentConfig(**_SUB_CFG),
    )


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


def make_synthesis_agent() -> BaseAgent:
    return BaseAgent(
        name="synthesis-agent",
        model=APP_CONFIG.base_model,
        gateway_mode="quality",  # final, highest-stakes reasoning
        instructions=(
            "You are the RETURNS DECISION SYNTHESISER. You receive the structured findings of "
            "four specialists (fraud, demand, marketing, ops) as JSON.\n"
            "Choose exactly ONE route:\n"
            "  - 'flag-for-review'  if fraud risk is material (fraud.flag_for_review is true or "
            "risk_score is high). This pauses for a human and OVERRIDES the other lanes. Set "
            "requires_human_approval=true.\n"
            "  - 'exchange-offer'   if NOT flagged and marketing.offer_incentive is true — keep "
            "the customer and the revenue with an exchange instead of a refund.\n"
            "  - 'reroute-label'    otherwise — accept the return but reroute the item to the "
            "high-deficit store the ops lane prepared, recovering reverse-logistics cost.\n"
            "Compute asset_recovery_usd as the value preserved: for reroute use ops "
            "estimated_savings_usd; for exchange add the retained order value; for flagged "
            "fraud use the order value protected from loss.\n\n"
            "Respond with ONLY a JSON object, no markdown, with EXACTLY these keys:\n"
            '{"route": "flag-for-review|exchange-offer|reroute-label", "headline": "<one line>", '
            '"customer_message": "<draft to customer>", "asset_recovery_usd": <float>, '
            '"requires_human_approval": <bool>, "reasoning": "<cite the signals>"}'
        ),
        output_schema=SynthesisDecision,
        enable_json_mode=False,
        temperature=0.3,
        memory_config=_no_mem(),
        config=AgentConfig(**_SUB_CFG),
    )


# ---------------------------------------------------------------------------
# Router + branch executor agents
# ---------------------------------------------------------------------------

BRANCH_NAMES = {
    "flag-for-review": "branch-fraud-review",
    "exchange-offer": "branch-exchange-offer",
    "reroute-label": "branch-reroute-label",
}


def make_branch_agents() -> dict[str, BaseAgent]:
    """The three terminal agents the RouterAgent dispatches to."""
    common = dict(
        model=APP_CONFIG.base_model,
        gateway_mode="modest",
        enable_json_mode=False,
        temperature=0.4,
        memory_config=_no_mem(),
        config=AgentConfig(**_SUB_CFG),
    )
    review = BaseAgent(
        name="branch-fraud-review",
        instructions=(
            "You are fraud-ops triage. A return was flagged for human review. Write a SHORT "
            "internal note (2-3 sentences) for the human reviewer: what was suspicious and "
            "exactly what to verify before approving. Do not address the customer."
        ),
        **common,
    )
    exchange = BaseAgent(
        name="branch-exchange-offer",
        instructions=(
            "You are a retention specialist. Write a warm, concise customer-facing message "
            "presenting the approved exchange incentive for their specific item. Reference the "
            "discount and the channel naturally. 2-3 sentences."
        ),
        **common,
    )
    reroute = BaseAgent(
        name="branch-reroute-label",
        instructions=(
            "You are a logistics concierge. Write a clear, friendly customer-facing message: "
            "their return is accepted and a prepaid label routes the item to a nearby store "
            "drop-off (mention the city and ETA) instead of a distant warehouse. 2-3 sentences."
        ),
        **common,
    )
    return {a.name: a for a in (review, exchange, reroute)}


def make_router() -> RouterAgent:
    return RouterAgent(
        name="returns-router",
        model=APP_CONFIG.base_model,
        gateway_mode="strict",  # cheap, deterministic-ish routing
        routes=[
            Route(
                agent_name="branch-fraud-review",
                description="route 'flag-for-review': suspected fraud, send to human review",
            ),
            Route(
                agent_name="branch-exchange-offer",
                description="route 'exchange-offer': offer the customer an exchange incentive",
            ),
            Route(
                agent_name="branch-reroute-label",
                description="route 'reroute-label': accept return and reroute item to a store",
            ),
        ],
        fallback_agent_name="branch-reroute-label",
        router_config=RouterConfig(routing_strategy="llm"),
        memory_config=_no_mem(),
        config=AgentConfig(**_SUB_CFG),
    )


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


@dataclass
class AgentBundle:
    fraud: BaseAgent
    demand: BaseAgent
    marketing: BaseAgent
    ops: BaseAgent
    synthesis: BaseAgent
    router: RouterAgent
    branches: dict[str, BaseAgent]

    def specialists(self) -> dict[str, BaseAgent]:
        return {
            "fraud": self.fraud,
            "demand": self.demand,
            "marketing": self.marketing,
            "ops": self.ops,
        }


def build_agents(tools: list[dict], tool_executor: Any, memory_enabled: bool) -> AgentBundle:
    return AgentBundle(
        fraud=make_fraud_agent(memory_enabled),
        demand=make_demand_agent(tools, tool_executor),
        marketing=make_marketing_agent(tools, tool_executor, memory_enabled),
        ops=make_ops_agent(tools, tool_executor),
        synthesis=make_synthesis_agent(),
        router=make_router(),
        branches=make_branch_agents(),
    )


def models_used_summary() -> dict[str, str]:
    """Human-readable mapping of lane -> gateway tier + why (for the API)."""
    out: dict[str, str] = {}
    for lane, (tier, why) in TIER_RATIONALE.items():
        resolved = "auto/quality" if tier == "quality" else "auto/cheap" if tier == "strict" else "auto/mid"
        gw = f" -> {resolved}" if APP_CONFIG.gateway_active else ""
        out[lane] = f"gateway_mode={tier}{gw} ({why})"
    return out
