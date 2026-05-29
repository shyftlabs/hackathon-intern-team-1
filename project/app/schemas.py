"""Pydantic models — the typed contracts that flow between agents.

Every specialist agent declares one of these as its ``output_schema`` so the
runner returns a validated instance on ``AgentResponse.structured_output``. No
LLM text parsing happens downstream.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Inbound request
# ---------------------------------------------------------------------------


class ReturnRequest(BaseModel):
    """A customer-initiated return, as received by the engine."""

    customer_id: str = Field(description="Stable customer identifier (USER memory scope key)")
    order_id: str
    sku: str = Field(description="Product SKU being returned, e.g. 'p1'")
    reason_text: str = Field(description="Free-text reason the customer gave")
    photo_base64: str | None = Field(
        default=None, description="Base64-encoded product photo (no data: prefix)"
    )
    customer_zip: str = Field(default="10001", description="Customer location for distance calc")
    item_name: str | None = None
    order_value: float | None = Field(default=None, description="Original order line value (USD)")


# ---------------------------------------------------------------------------
# Specialist agent outputs (the four parallel lanes)
# ---------------------------------------------------------------------------


class FraudAssessment(BaseModel):
    """Fraud lane — vision inspection + return-history cross-reference."""

    risk_score: float = Field(ge=0.0, le=1.0, description="0=clean, 1=almost certainly fraud")
    damage_level: str = Field(description="none | minor | moderate | severe")
    consistent_with_reason: bool = Field(
        description="Does the photo match the stated return reason?"
    )
    flag_for_review: bool = Field(description="True if a human should review before refunding")
    flag_reason: str = Field(description="Short explanation for the risk/flag")
    observations: str = Field(description="What the vision model saw in the photo")


class StoreDeficit(BaseModel):
    store_id: str
    city: str
    distance_km: float
    current_stock: int
    deficit_score: float = Field(description="Higher = more unmet local demand for this SKU")
    trend_velocity: float = Field(description="Units sold/week locally")


class DemandMap(BaseModel):
    """Demand lane — regional stock + trend velocity from the inventory MCP."""

    best_store_id: str = Field(description="Highest-deficit store within radius")
    best_store_deficit: float
    distance_km: float = Field(description="Distance to the best store from the customer")
    stores: list[StoreDeficit] = Field(default_factory=list)
    summary: str


class IncentiveDecision(BaseModel):
    """Marketing lane — LTV-driven exchange incentive."""

    offer_incentive: bool
    offer_type: str = Field(description="none | exchange_coupon | loyalty_credit | upgrade")
    discount_pct: float = Field(ge=0.0, le=100.0)
    channel: str = Field(description="email | sms | app_push")
    ltv_tier: str = Field(description="platinum | gold | silver | bronze | unknown")
    message: str = Field(description="Draft of the offer message to the customer")
    rationale: str


class OpsDecision(BaseModel):
    """Ops lane — reroute label generation + inventory write (MCP tools)."""

    reroute: bool = Field(description="True if rerouting to a store beats central return")
    target_store_id: str
    label_id: str
    label_url: str
    carrier: str
    eta_days: int
    inventory_after: int = Field(description="Target store stock after the +1 write")
    estimated_savings_usd: float = Field(description="Reverse-logistics saved vs central warehouse")
    rationale: str


# ---------------------------------------------------------------------------
# Synthesis output (slim — sub-decisions are attached in code)
# ---------------------------------------------------------------------------


class SynthesisDecision(BaseModel):
    """The synthesis agent's merged verdict (its ``output_schema``).

    Kept intentionally slim: the four sub-decisions are attached by the engine
    from the specialists' structured outputs, so the LLM only has to reason
    about the routing call and the customer narrative.
    """

    route: str = Field(description="flag-for-review | exchange-offer | reroute-label")
    headline: str = Field(description="One-line operational summary")
    customer_message: str = Field(description="Draft customer-facing message")
    asset_recovery_usd: float = Field(description="Estimated value recovered by this decision")
    requires_human_approval: bool
    reasoning: str = Field(description="Why this route, citing the specialist signals")


# ---------------------------------------------------------------------------
# Engine result (API response shape — not an LLM schema)
# ---------------------------------------------------------------------------

VALID_ROUTES = ("flag-for-review", "exchange-offer", "reroute-label")


class ReturnResult(BaseModel):
    """Everything the API hands back for one processed return."""

    return_id: str
    status: str = Field(description="COMPLETED | PENDING_APPROVAL | REJECTED")
    route: str
    headline: str
    customer_message: str
    asset_recovery_usd: float
    requires_human_approval: bool
    reasoning: str

    # Specialist sub-decisions
    fraud: FraudAssessment | None = None
    demand: DemandMap | None = None
    incentive: IncentiveDecision | None = None
    ops: OpsDecision | None = None

    # The model-abstraction story: which gateway tier handled each lane
    models_used: dict[str, str] = Field(default_factory=dict)

    # Observability + artifacts
    trace_id: str | None = None
    trace_url: str | None = None
    latency_ms: int = 0
    artifacts: dict | None = None
    branch_output: str | None = Field(default=None, description="RouterAgent branch agent message")
    memory_used: bool = False
