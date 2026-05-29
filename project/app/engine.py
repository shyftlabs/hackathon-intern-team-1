"""ReturnsEngine — the multi-agent orchestrator.

Lifecycle:
    engine = ReturnsEngine(); await engine.initialize()
    result = await engine.process_return(ReturnRequest(...))
    ...
    await engine.close()

One ``process_return`` call mirrors the playground ParallelCoordinatorAgent
pattern (suppress per-sub-agent session logging, run lanes in branch contexts,
do one ``save_turn`` at the end) but fans out with ``asyncio.gather`` so each
lane can take a *different* input — critically, only the Fraud lane receives the
multimodal photo, and each lane runs on its own gateway tier.

Every lane is resilient: if an LLM lane fails or returns unparseable output, the
engine falls back to deterministic numbers computed directly from the seed data,
so the demo always produces a coherent decision.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from orchestrator import (
    AgentRunner,
    MCPServerStdio,
    MCPUtil,
    RunnerConfig,
    ToolExecutor,
    get_logger,
)
from orchestrator.agent.types import AgentResponse

from app import seed_data
from app.agents import BRANCH_NAMES, AgentBundle, build_agents, models_used_summary
from app.config import APP_CONFIG, APP_DIR, MCP_SERVER_SCRIPT, VENV_PYTHON
from app.memory_seed import maybe_seed
from app.schemas import (
    VALID_ROUTES,
    DemandMap,
    FraudAssessment,
    IncentiveDecision,
    OpsDecision,
    ReturnRequest,
    ReturnResult,
    StoreDeficit,
    SynthesisDecision,
)

logger = get_logger(__name__)


def _sniff_mime(b64: str) -> str:
    """Detect the image media type from the base64 payload's magic bytes.

    Anthropic (and others) validate the declared data-URI media type against the
    actual bytes, so we must not hardcode one. Defaults to image/jpeg.
    """
    import base64 as _b64

    try:
        head = _b64.b64decode(b64[:24] + "==", validate=False)[:12]
    except Exception:
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


# Tools each lane is allowed to see (names match the MCP server).
_DEMAND_TOOLS = {"get_regional_demand"}
_MARKETING_TOOLS = {"get_customer_history"}
_OPS_TOOLS = {"get_regional_demand", "generate_reroute_label", "update_inventory"}


@dataclass
class PendingApproval:
    result: ReturnResult
    request: ReturnRequest
    demand: DemandMap | None
    ops: OpsDecision | None
    session_id: str | None


@dataclass
class ReturnsEngine:
    bundle: AgentBundle | None = None
    runner: AgentRunner | None = None
    container: Any = None
    memory_enabled: bool = False
    _server: MCPServerStdio | None = None
    _executor: ToolExecutor | None = None
    _tools: list[dict] = field(default_factory=list)
    _lifecycle: Any = None
    _initialized: bool = False
    _pending: dict[str, PendingApproval] = field(default_factory=dict)

    # ------------------------------------------------------------------ setup
    async def initialize(self) -> None:
        if self._initialized:
            return
        from orchestrator.core.container import get_container
        from orchestrator.core.lifecycle import get_lifecycle_manager

        self._lifecycle = get_lifecycle_manager(
            fail_on_unhealthy=False,  # degrade gracefully if langfuse/memory is down
            verify_connections=True,
            enable_signal_handlers=False,
        )
        await self._lifecycle.initialize()
        self.container = get_container()

        self._wire_memory()
        await self._connect_mcp()

        self.bundle = build_agents(
            tools=self._tools, tool_executor=self._executor, memory_enabled=self.memory_enabled
        )
        # Give each tool-using lane only the tools it needs.
        self.bundle.demand.tools = self._tools_for(_DEMAND_TOOLS)
        self.bundle.marketing.tools = self._tools_for(_MARKETING_TOOLS)
        self.bundle.ops.tools = self._tools_for(_OPS_TOOLS)

        self.runner = AgentRunner(
            container=self.container,
            tool_executor=self._executor,
            config=RunnerConfig(persist_state=False, default_max_turns=APP_CONFIG.max_turns),
        )

        if self.memory_enabled:
            try:
                n = await maybe_seed(self.container.memory_client)
                logger.info(f"Seeded long-term memory for {n} customers")
            except Exception as exc:
                logger.warning(f"memory seeding skipped: {exc}")

        self._initialized = True
        logger.info(
            "ReturnsEngine ready "
            f"(gateway={'on' if APP_CONFIG.gateway_active else 'off'}, "
            f"memory={'on' if self.memory_enabled else 'off'}, tools={len(self._tools)})"
        )

    def _wire_memory(self) -> None:
        if not APP_CONFIG.enable_memory:
            self.memory_enabled = False
            return
        if APP_CONFIG.use_intelligent_memory:
            try:
                from orchestrator.memory import (
                    IntelligenceConfig,
                    IntelligentMemoryClient,
                    MemoryConfig,
                )

                client = IntelligentMemoryClient(
                    config=MemoryConfig(), intelligence_config=IntelligenceConfig()
                )
                self.container.set_memory_client(client)
                logger.info("Using IntelligentMemoryClient (importance scoring + decay)")
            except Exception as exc:
                logger.warning(f"IntelligentMemoryClient unavailable, using default: {exc}")
        mc = self.container.memory_client
        self.memory_enabled = bool(mc is not None and getattr(mc, "is_enabled", False))
        if APP_CONFIG.enable_memory and not self.memory_enabled:
            logger.warning(
                "Memory requested but not enabled (Milvus/embedder unreachable?) — "
                "agents will run without long-term memory"
            )

    async def _connect_mcp(self) -> None:
        self._server = MCPServerStdio(
            params={"command": VENV_PYTHON, "args": [MCP_SERVER_SCRIPT], "cwd": str(APP_DIR)},
            client_session_timeout_seconds=20,
            name="inventory-logistics",
        )
        await self._server.connect()
        raw = await MCPUtil.get_function_tools(self._server)
        self._tools = [self._as_tool_dict(t) for t in raw]
        self._executor = ToolExecutor({self._server: None})
        await self._executor.initialize()
        names = [t["function"]["name"] for t in self._tools]
        logger.info(f"MCP connected: {len(names)} tools: {', '.join(names)}")

    @staticmethod
    def _as_tool_dict(t: Any) -> dict:
        if isinstance(t, dict):
            return t
        if hasattr(t, "model_dump"):
            return t.model_dump()
        return {
            "type": "function",
            "function": {
                "name": getattr(t, "name", str(t)),
                "description": getattr(t, "description", ""),
                "parameters": getattr(t, "parameters", {}),
            },
        }

    def _tools_for(self, allowed: set[str]) -> list[dict]:
        return [t for t in self._tools if t.get("function", {}).get("name") in allowed]

    # ------------------------------------------------------------- processing
    async def process_return(
        self, req: ReturnRequest, conversation_id: str | None = None
    ) -> ReturnResult:
        if not self._initialized:
            await self.initialize()
        assert self.bundle and self.runner

        return_id = conversation_id or f"ret_{uuid.uuid4().hex[:12]}"
        trace_id = uuid.uuid4().hex
        started = time.monotonic()

        # Open a Redis session for short-term state (photo/reason/order id).
        session_id = await self._open_session(req.customer_id, return_id)

        item_name = req.item_name or seed_data.product_info(req.sku)["name"]
        order_value = (
            req.order_value if req.order_value is not None else seed_data.product_info(req.sku)["price"]
        )

        # ---- Fan out the four lanes concurrently (each on its own tier) -----
        fraud_input = self._fraud_input(req, item_name, order_value)
        demand_input = (
            f"SKU {req.sku} ({item_name}). Customer ZIP {req.customer_zip}. "
            f"Radius {APP_CONFIG.reroute_radius_km} km. Identify the highest-deficit nearby "
            "store to reroute this returned item to."
        )
        marketing_input = (
            f"Customer {req.customer_id} is returning {item_name} (SKU {req.sku}, order value "
            f"${order_value:.2f}). Reason: '{req.reason_text}'. Decide on an exchange incentive."
        )
        ops_input = (
            f"SKU {req.sku} ({item_name}), customer ZIP {req.customer_zip}, "
            f"radius {APP_CONFIG.reroute_radius_km} km. Find the best reroute store, generate the "
            "prepaid label, and credit the unit to that store."
        )

        lanes = await asyncio.gather(
            self._run_lane(self.bundle.fraud, fraud_input, req, trace_id, return_id),
            self._run_lane(self.bundle.demand, demand_input, req, trace_id, return_id),
            self._run_lane(self.bundle.marketing, marketing_input, req, trace_id, return_id),
            self._run_lane(self.bundle.ops, ops_input, req, trace_id, return_id),
            return_exceptions=True,
        )
        fraud = self._coerce_fraud(lanes[0], req, order_value)
        demand = self._coerce_demand(lanes[1], req)
        incentive = self._coerce_incentive(lanes[2], req, item_name, order_value)
        ops = self._coerce_ops(lanes[3], req)
        # Capture the Ops lane's MCP artifacts from its OWN response — the shared
        # executor's run_artifacts buffer is cleared by later runs (synthesis/branch).
        ops_artifacts = (
            getattr(lanes[3], "run_artifacts", None) if not isinstance(lanes[3], Exception) else None
        )

        # ---- Synthesis: merge the four structured signals -------------------
        decision = await self._synthesize(
            req, item_name, order_value, fraud, demand, incentive, ops, trace_id, return_id
        )
        route = self._resolve_route(decision, fraud, incentive)

        # ---- RouterAgent branch + branch executor ---------------------------
        branch_output = await self._run_branch(
            route, req, item_name, decision, incentive, ops, demand, trace_id, return_id
        )

        requires_approval = route == "flag-for-review"
        status = "PENDING_APPROVAL" if requires_approval else "COMPLETED"
        customer_message = (
            "Thanks — your return is being reviewed and we'll be in touch shortly."
            if requires_approval
            else (branch_output or decision.customer_message)
        )

        result = ReturnResult(
            return_id=return_id,
            status=status,
            route=route,
            headline=decision.headline,
            customer_message=customer_message,
            asset_recovery_usd=round(decision.asset_recovery_usd, 2),
            requires_human_approval=requires_approval,
            reasoning=decision.reasoning,
            fraud=fraud,
            demand=demand,
            incentive=incentive,
            ops=ops,
            models_used=models_used_summary(),
            trace_id=trace_id,
            trace_url=APP_CONFIG.trace_url(trace_id),
            latency_ms=int((time.monotonic() - started) * 1000),
            artifacts=ops_artifacts or self._artifacts_snapshot(),
            branch_output=branch_output,
            memory_used=self.memory_enabled,
        )

        # One clean session save for the whole turn.
        await self._save_turn(session_id, req, result)

        if requires_approval:
            self._pending[return_id] = PendingApproval(
                result=result, request=req, demand=demand, ops=ops, session_id=session_id
            )
        return result

    # ---------------------------------------------------------- approval gate
    async def resolve_approval(
        self, return_id: str, approved: bool, reviewer: str = "ops-team"
    ) -> ReturnResult:
        pend = self._pending.get(return_id)
        if not pend:
            raise KeyError(f"No pending approval for {return_id!r}")
        assert self.runner and self.bundle
        result = pend.result

        if not approved:
            result.status = "REJECTED"
            result.route = "flag-for-review"
            result.customer_message = (
                "After review, we're unable to accept this return. Our team has reached out with "
                "details and next steps."
            )
            result.reasoning += f" | Human reviewer ({reviewer}) REJECTED the return."
        else:
            # Approved: fall through to the reroute resolution.
            result.status = "COMPLETED"
            result.route = "reroute-label"
            result.requires_human_approval = False
            result.headline = "Return approved after human review — item rerouted to nearby store"
            ops = pend.ops
            branch_input = self._reroute_branch_input(pend.request, ops, pend.demand)
            branch = await self.runner.run(
                self.bundle.branches[BRANCH_NAMES["reroute-label"]],
                input=branch_input,
                user_id=pend.request.customer_id,
                session_id=None,
                conversation_id=return_id,
            )
            result.customer_message = branch.content or result.customer_message
            result.branch_output = branch.content
            result.reasoning += f" | Human reviewer ({reviewer}) APPROVED; rerouting item."
            if ops:
                result.asset_recovery_usd = round(ops.estimated_savings_usd, 2)

        await self._save_turn(pend.session_id, pend.request, result)
        self._pending.pop(return_id, None)
        return result

    def pending_ids(self) -> list[str]:
        return list(self._pending.keys())

    # ----------------------------------------------------------------- lanes
    def _fraud_input(self, req: ReturnRequest, item_name: str, order_value: float) -> Any:
        text = (
            f"Customer: {req.customer_id}. Returning {item_name} (SKU {req.sku}), order "
            f"{req.order_id}, value ${order_value:.2f}. Stated reason: '{req.reason_text}'. "
            "Inspect the attached photo (if any), judge consistency with the reason, and factor "
            "in the customer's history from memory."
        )
        if req.photo_base64:
            data_uri = f"data:{_sniff_mime(req.photo_base64)};base64,{req.photo_base64}"
            return [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ]
        return text + " NOTE: no photo was uploaded with this return."

    async def _run_lane(
        self, agent, agent_input, req: ReturnRequest, trace_id: str, return_id: str
    ) -> AgentResponse:
        # session_id=None so sub-agents never load/save session; user_id drives
        # USER-scope memory; one shared trace_id groups the whole return.
        return await self.runner.run(
            agent,
            input=agent_input,
            user_id=req.customer_id,
            session_id=None,
            conversation_id=return_id,
            trace_id=trace_id,
        )

    # --------------------------------------------------------- result coercion
    @staticmethod
    def _struct(resp: Any):
        return getattr(resp, "structured_output", None) if not isinstance(resp, Exception) else None

    def _coerce_fraud(self, resp, req: ReturnRequest, order_value: float) -> FraudAssessment:
        s = self._struct(resp)
        if isinstance(s, FraudAssessment):
            return s
        # Deterministic fallback from CRM history.
        hist = seed_data.customer_history(req.customer_id)
        flags = hist.get("fraud_flags", 0)
        risk = min(0.3 + 0.3 * flags + (0.1 if not req.photo_base64 else 0.0), 0.95)
        flagged = flags >= 1 or risk >= APP_CONFIG.fraud_review_threshold
        detail = "" if not isinstance(resp, Exception) else f" (lane error: {resp})"
        return FraudAssessment(
            risk_score=round(risk, 2),
            damage_level="unknown",
            consistent_with_reason=not flagged,
            flag_for_review=flagged,
            flag_reason=(f"{flags} prior fraud flag(s) on file" if flags else "heuristic fallback"),
            observations=f"Fraud lane fallback used{detail}.",
        )

    def _coerce_demand(self, resp, req: ReturnRequest) -> DemandMap:
        s = self._struct(resp)
        if isinstance(s, DemandMap) and s.stores:
            return s
        data = seed_data.regional_demand(
            req.sku, req.customer_zip, float(APP_CONFIG.reroute_radius_km)
        )
        stores = [
            StoreDeficit(
                store_id=r["store_id"],
                city=r["city"],
                distance_km=r["distance_km"],
                current_stock=r["current_stock"],
                deficit_score=r["deficit_score"],
                trend_velocity=r["trend_velocity"],
            )
            for r in data["stores"]
        ]
        best = stores[0] if stores else None
        return DemandMap(
            best_store_id=best.store_id if best else "",
            best_store_deficit=best.deficit_score if best else 0.0,
            distance_km=best.distance_km if best else 0.0,
            stores=stores,
            summary=(
                f"{best.city} ({best.store_id}) has the highest unmet demand for {req.sku}."
                if best
                else "No stores within radius."
            ),
        )

    def _coerce_incentive(
        self, resp, req: ReturnRequest, item_name: str, order_value: float
    ) -> IncentiveDecision:
        s = self._struct(resp)
        if isinstance(s, IncentiveDecision):
            return s
        hist = seed_data.customer_history(req.customer_id)
        tier = hist.get("ltv_tier", "unknown")
        good = tier in ("platinum", "gold") and hist.get("fraud_flags", 0) == 0
        disc = {"platinum": 25.0, "gold": 15.0}.get(tier, 0.0) if good else 0.0
        return IncentiveDecision(
            offer_incentive=good,
            offer_type="exchange_coupon" if good else "none",
            discount_pct=disc,
            channel=hist.get("preferred_channel", "email"),
            ltv_tier=tier,
            message=(
                f"As a valued {tier} member, enjoy {disc:.0f}% off an exchange for {item_name}."
                if good
                else "No incentive recommended."
            ),
            rationale="Marketing lane fallback (LTV-tier heuristic).",
        )

    def _coerce_ops(self, resp, req: ReturnRequest) -> OpsDecision:
        s = self._struct(resp)
        if isinstance(s, OpsDecision) and s.label_id:
            return s
        data = seed_data.regional_demand(
            req.sku, req.customer_zip, float(APP_CONFIG.reroute_radius_km)
        )
        store_id = data.get("best_store_id") or ""
        if not store_id:
            return OpsDecision(
                reroute=False,
                target_store_id="",
                label_id="",
                label_url="",
                carrier="",
                eta_days=0,
                inventory_after=0,
                estimated_savings_usd=0.0,
                rationale="No reroute target within radius (ops fallback).",
            )
        label = seed_data.reroute_label(req.sku, store_id, req.customer_zip)
        inv = seed_data.apply_inventory(store_id, req.sku, 1)
        return OpsDecision(
            reroute=True,
            target_store_id=store_id,
            label_id=label["label_id"],
            label_url=label["label_url"],
            carrier=label["carrier"],
            eta_days=label["eta_days"],
            inventory_after=inv["new_stock"],
            estimated_savings_usd=label["estimated_savings_usd"],
            rationale=f"Ops fallback: rerouted to {label['to_city']} ({store_id}).",
        )

    # ------------------------------------------------------------- synthesis
    async def _synthesize(
        self, req, item_name, order_value, fraud, demand, incentive, ops, trace_id, return_id
    ) -> SynthesisDecision:
        payload = {
            "return": {
                "customer_id": req.customer_id,
                "sku": req.sku,
                "item": item_name,
                "order_value": order_value,
                "reason": req.reason_text,
            },
            "fraud": fraud.model_dump(),
            "demand": demand.model_dump(),
            "marketing": incentive.model_dump(),
            "ops": ops.model_dump(),
        }
        synth_input = (
            "Here are the four specialist findings for this return:\n"
            f"{json.dumps(payload, indent=2)}\n\n"
            "Decide the single best route and produce the decision JSON."
        )
        try:
            resp = await self.runner.run(
                self.bundle.synthesis,
                input=synth_input,
                user_id=req.customer_id,
                session_id=None,
                conversation_id=return_id,
                trace_id=trace_id,
            )
            s = resp.structured_output
            if isinstance(s, SynthesisDecision):
                return s
        except Exception as exc:
            logger.warning(f"synthesis lane failed, using deterministic decision: {exc}")
        return self._fallback_decision(fraud, demand, incentive, ops, order_value)

    def _fallback_decision(self, fraud, demand, incentive, ops, order_value) -> SynthesisDecision:
        if fraud.flag_for_review or fraud.risk_score >= APP_CONFIG.fraud_review_threshold:
            return SynthesisDecision(
                route="flag-for-review",
                headline="Return flagged for human fraud review",
                customer_message="Your return is being reviewed.",
                asset_recovery_usd=order_value,
                requires_human_approval=True,
                reasoning=f"Fraud risk {fraud.risk_score}: {fraud.flag_reason}.",
            )
        if incentive.offer_incentive:
            return SynthesisDecision(
                route="exchange-offer",
                headline="Exchange incentive offered",
                customer_message=incentive.message,
                asset_recovery_usd=round(order_value + ops.estimated_savings_usd, 2),
                requires_human_approval=False,
                reasoning=f"{incentive.ltv_tier} customer; {incentive.discount_pct:.0f}% exchange offer.",
            )
        return SynthesisDecision(
            route="reroute-label",
            headline="Item rerouted to high-demand store",
            customer_message="Your return is accepted with a prepaid label to a nearby store.",
            asset_recovery_usd=ops.estimated_savings_usd,
            requires_human_approval=False,
            reasoning=f"Reroute to {ops.target_store_id} saves ${ops.estimated_savings_usd:.2f}.",
        )

    def _resolve_route(
        self, decision: SynthesisDecision, fraud: FraudAssessment, incentive: IncentiveDecision
    ) -> str:
        # Fraud is an authoritative safety override.
        if fraud.flag_for_review or fraud.risk_score >= APP_CONFIG.fraud_review_threshold:
            return "flag-for-review"
        route = (decision.route or "").strip().lower()
        if route in VALID_ROUTES:
            return route
        return "exchange-offer" if incentive.offer_incentive else "reroute-label"

    # ---------------------------------------------------------- router branch
    async def _run_branch(
        self, route, req, item_name, decision, incentive, ops, demand, trace_id, return_id
    ) -> str | None:
        # Use the real RouterAgent to pick the branch (corroborates the route),
        # then run that branch's executor agent for the customer-facing copy.
        chosen = await self._route_via_router(route, decision)
        target = self.bundle.branches.get(chosen) or self.bundle.branches[BRANCH_NAMES[route]]

        if route == "exchange-offer":
            branch_input = (
                f"Customer is returning {item_name}. Approved incentive: {incentive.offer_type} "
                f"{incentive.discount_pct:.0f}% via {incentive.channel}. Draft note: "
                f"'{incentive.message}'."
            )
        elif route == "reroute-label":
            branch_input = self._reroute_branch_input(req, ops, demand)
        else:  # flag-for-review
            branch_input = (
                f"Return for {item_name} (order {req.order_id}) flagged. Risk reason: "
                f"{decision.reasoning}. Write the internal reviewer note."
            )
        try:
            resp = await self.runner.run(
                target,
                input=branch_input,
                user_id=req.customer_id,
                session_id=None,
                conversation_id=return_id,
                trace_id=trace_id,
            )
            return resp.content
        except Exception as exc:
            logger.warning(f"branch agent failed: {exc}")
            return decision.customer_message

    async def _route_via_router(self, route: str, decision: SynthesisDecision) -> str | None:
        try:
            llm = self.container.llm_client
            router_input = (
                f"The synthesised decision selected route '{route}'. Headline: {decision.headline}. "
                "Pick the matching branch."
            )
            chosen = await self.bundle.router.route(router_input, llm_client=llm)
            if chosen in self.bundle.branches:
                logger.info(f"RouterAgent → {chosen}")
                return chosen
        except Exception as exc:
            logger.warning(f"RouterAgent.route failed, using deterministic branch: {exc}")
        return BRANCH_NAMES.get(route)

    def _reroute_branch_input(
        self, req: ReturnRequest, ops: OpsDecision | None, demand: DemandMap | None
    ) -> str:
        item = req.item_name or seed_data.product_info(req.sku)["name"]
        city = ""
        target = ops.target_store_id if ops else ""
        if demand and demand.stores:
            city = next((s.city for s in demand.stores if s.store_id == target), demand.stores[0].city)
        eta = ops.eta_days if ops else 2
        return (
            f"Customer is returning {item}. Their item will be rerouted to the {city or 'nearby'} "
            f"store, ETA {eta} day(s), via a prepaid label. Write the friendly confirmation."
        )

    # ----------------------------------------------------------- session/io
    async def _open_session(self, user_id: str, return_id: str) -> str | None:
        if not APP_CONFIG.enable_session:
            return None
        sc = getattr(self.container, "session_client", None)
        if not sc or not getattr(sc, "is_enabled", False):
            return None
        try:
            return await sc.get_or_create_session(user_id=user_id, conversation_id=return_id)
        except Exception as exc:
            logger.warning(f"session open failed: {exc}")
            return None

    async def _save_turn(
        self, session_id: str | None, req: ReturnRequest, result: ReturnResult
    ) -> None:
        if not session_id or not self.runner:
            return
        try:
            await self.runner.save_turn(
                session_id=session_id,
                user_message=f"Return request: {req.sku} (order {req.order_id}) — {req.reason_text}",
                assistant_message=f"[{result.route}] {result.headline}",
                agent=None,
            )
        except Exception as exc:
            logger.warning(f"save_turn failed: {exc}")

    def _artifacts_snapshot(self) -> dict | None:
        try:
            ra = getattr(self._executor, "run_artifacts", None)
            if ra and not ra.is_empty():
                return ra.to_dict()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ close
    async def close(self) -> None:
        if self._server:
            try:
                await self._server.cleanup()
            except Exception:
                pass
        if self._lifecycle:
            try:
                await self._lifecycle.shutdown()
            except Exception:
                pass
        self._initialized = False
