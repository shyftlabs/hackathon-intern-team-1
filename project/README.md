# Automated Omnichannel Returns Optimization & Reallocation Engine

A multi-agent loop, built on the **ShyftLabs Continuum** framework, that runs at
the moment a customer requests a return. It inspects the product photo for fraud,
computes real-time regional demand, decides whether to offer an exchange, and
orchestrates physically rerouting the item to a nearby high-demand store instead
of shipping it blindly back to a distant warehouse.

> **Status: working end-to-end.** All three decision branches, the vision lane,
> long-term memory, MCP tools, run artifacts, and the human-in-the-loop approval
> gate have been verified live against the Smart Gateway + Redis + Milvus.

---

## The idea in one diagram

```
  Return request (customer_id, order_id, sku, reason, photo)
                          │
                          ▼
        ┌──────────  ReturnsEngine (coordinator)  ──────────┐
        │   loads Redis session + Milvus long-term memory   │
        │            fans out to 4 lanes in parallel         │
        └───────────────────────────────────────────────────┘
            │            │             │            │
   ┌────────▼──┐  ┌──────▼────┐  ┌─────▼─────┐  ┌───▼────────┐
   │  Fraud    │  │  Demand   │  │ Marketing │  │    Ops     │
   │ quality   │  │  strict   │  │  modest   │  │   modest   │
   │ (vision)  │  │ (cheap)   │  │ (memory)  │  │ (MCP+label)│
   └────────┬──┘  └──────┬────┘  └─────┬─────┘  └───┬────────┘
            └─────────────┴──────┬──────┴────────────┘
                                 ▼
                  Synthesis agent (quality)  →  ReturnDecision
                                 ▼
                  RouterAgent branch (3 routes)
                ┌────────────────┼────────────────┐
                ▼                ▼                 ▼
       flag-for-review     exchange-offer     reroute-label
       (human approval)    (retain revenue)   (recover logistics $)
```

Each lane runs on its **own Smart-Gateway tier** — the gateway picks the cheapest
model that clears the bar. This is the model-abstraction pitch, and it is real:
in a live run the synthesis lane resolves to `auto/quality → claude-opus`, the
demand lane to `auto/cheap`, and the branch agents to `auto/mid → claude-sonnet`.

---

## Quick start

Prerequisites (already true in this environment):
- The Continuum framework + venv at `../continuum/.venv` (Python 3.13).
- `SMART_GATEWAY_URL` / `SMART_GATEWAY_API_KEY` set in `.env` (this project ships one).
- Infra up via `docker compose` in `../continuum`: Redis (:6380) + Milvus (:19530).
  *(Optional: Langfuse :3000 for the trace link, Temporal :7233 for the durable gate.)*

### Web demo
```bash
./run.sh                 # → http://127.0.0.1:8099
```
First start seeds long-term memory for the demo customers (~1 min). Then open the
URL and click a scenario chip:
- **Platinum · genuine** → *exchange-offer* (memory-driven 20% incentive)
- **Walk-in · refund** → *reroute-label* (no incentive, item rerouted, $ saved)
- **Serial returner · suspicious** → *flag-for-review* → click **Approve** to resume

### Headless CLI demo (no server)
```bash
./run_cli.sh             # runs all three scenarios + auto-approves the flagged one
./run_cli.sh frank       # just the fraud / human-in-the-loop scenario
```

---

## How Continuum powers it

| Capability | Where | What it does |
|---|---|---|
| **Model abstraction** | `gateway_mode` per agent (`app/agents.py`) | quality→`auto/quality` (vision), strict→`auto/cheap`, modest→`auto/mid`. One endpoint, 250+ models, cheapest-that-clears-the-bar. |
| **Parallel fan-out** | `ReturnsEngine.process_return` (`app/engine.py`) | 4 lanes run concurrently with `asyncio.gather`; only the Fraud lane gets the multimodal photo. |
| **Vision** | Fraud agent | `runner.run(input=[{role,content:[text, image_url]}])` — inspects the photo, judges consistency with the stated reason. |
| **Structured output** | `output_schema=` Pydantic on every agent | validated `FraudAssessment` / `DemandMap` / `IncentiveDecision` / `OpsDecision` / `SynthesisDecision` — no text parsing downstream. |
| **MCP tools** | `app/mcp_server.py` (FastMCP stdio) | `get_regional_demand`, `get_customer_history`, `generate_reroute_label`, `update_inventory`. |
| **Run artifacts** | Ops lane | the reroute label lands on `AgentResponse.run_artifacts` (captured from the ops lane's own response). |
| **Long-term memory** | `IntelligentMemoryClient` (Milvus+mem0) | USER-scope fraud history / LTV / channel, seeded at startup; Fraud + Marketing search it. Importance scoring + decay down-weight stale signals. |
| **Short-term memory** | Redis session | one `save_turn` per return summarizes the decision. |
| **Router branch** | real `RouterAgent.route()` | dispatches to one of three branch executor agents for the customer-facing copy. |
| **Human-in-the-loop** | in-process approval store (`app/engine.py`) | flagged returns park as `PENDING_APPROVAL`; `POST /returns/{id}/approve` resumes. |
| **Durable approval (optional)** | `temporal_workflow.py` | the same gate as a crash-safe Temporal `agent→approval→agent` workflow. |
| **Observability** | Langfuse | one shared `trace_id` per return; the API returns a clickable trace URL. |

---

## API

| Method | Path | Body / params | Returns |
|---|---|---|---|
| `GET`  | `/` | — | demo UI |
| `POST` | `/return` | `ReturnRequest` JSON | `ReturnResult` (route, 4 lane outputs, models_used, trace, artifacts) |
| `POST` | `/returns/{id}/approve` | `{"reviewer": "..."}` | resumed `ReturnResult` |
| `POST` | `/returns/{id}/reject` | `{"reviewer": "..."}` | rejected `ReturnResult` |
| `GET`  | `/pending` | — | ids awaiting approval |
| `GET`  | `/samples` | — | seed customers/products + sample photos |
| `GET`  | `/health` | — | engine + subsystem status |

`ReturnRequest`: `{customer_id, order_id, sku, reason_text, photo_base64?, customer_zip?, item_name?, order_value?}`

```bash
curl -s -X POST localhost:8099/return -H 'Content-Type: application/json' -d '{
  "customer_id":"cust_frank","order_id":"ORD-1","sku":"p3",
  "reason_text":"Earbuds defective.","customer_zip":"07302"}'
```

---

## File map

```
project/
├── run.sh / run_cli.sh        # launchers (use ../continuum/.venv)
├── run_demo.py                # headless 3-scenario demo
├── temporal_workflow.py       # OPTIONAL durable approval gate (needs temporalio)
├── requirements.txt
└── app/
    ├── __init__.py            # .env bootstrap (must precede orchestrator import)
    ├── config.py              # AppConfig + gateway/model/toggle settings
    ├── schemas.py             # Pydantic contracts (request, 4 lanes, decision, result)
    ├── seed_data.py           # stores/inventory/demand + customers + geo (one source of truth)
    ├── mcp_server.py          # FastMCP stdio server (4 tools)
    ├── memory_seed.py         # seeds USER-scope customer history into long-term memory
    ├── sample_image.py        # pure-Python PNG generator (clean / damaged product)
    ├── agents.py              # 5 specialists + RouterAgent + 3 branch agents, per-tier
    ├── engine.py              # ReturnsEngine: lifecycle, MCP, coordinator, synthesis, router, approval
    └── api.py                 # Starlette app + single-page demo UI
```

---

## Design notes & resilience

- **Why a custom coordinator, not `ParallelAgent`?** `ParallelAgent` gives the
  same input to every sub-agent and merges their `.content` strings. Our lanes
  need *different* inputs (only Fraud gets the photo) and *typed* outputs, so the
  engine fans out with `asyncio.gather` over `runner.run`, mirroring the playground
  `ParallelCoordinatorAgent` pattern (suppress per-sub-agent session logging; one
  `save_turn` at the end) while keeping per-lane control.
- **Resilient lanes.** Every lane has a deterministic fallback computed directly
  from the seed data, so an LLM hiccup never breaks the decision — the demand and
  ops numbers are always coherent.
- **Structured output across tiers.** `enable_json_mode=True` only works on the
  `strict`/cheap tier (mid/quality resolve to thinking models that reject
  `response_format`), so quality/modest agents use `output_schema` +
  `enable_json_mode=False` and the runner parses the JSON client-side.
- **Fraud is a safety override.** A flagged fraud signal forces `flag-for-review`
  regardless of the other lanes. A high-value item returned with **no photo** is
  itself a flag trigger.
- **Graceful degradation.** If Milvus/embedder is down, memory disables itself and
  agents still run. If Langfuse is down, you still get the `trace_id`. If Temporal
  isn't installed, the in-process approval gate covers human-in-the-loop.

### Tunable env (in `.env` or the shell)
`MEMORY_ENABLED`, `USE_INTELLIGENT_MEMORY`, `LANGFUSE_ENABLED`, `RETURNS_BASE_MODEL`,
`RETURNS_VISION_MODEL`, `RETURNS_RADIUS_KM`, `RETURNS_FRAUD_THRESHOLD`.
