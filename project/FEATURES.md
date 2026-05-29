# Returns Engine — Differentiators & Feature Roadmap

## How This Differs from Existing Returns Solutions

### What existing platforms do (Narvar, Loop Returns, Happy Returns, AfterShip)

| Capability | Existing Tools | This Engine |
|---|---|---|
| Return routing | Central warehouse only | Demand-signal rerouting to highest-deficit store |
| Fraud detection | Manual review queues or simple rule thresholds | Vision model inspects photos + cross-references behavioral memory |
| Customer incentives | Fixed policy rules (e.g. "gold members get 10% off") | LTV-tier agent reasons over order value, history, and fraud signal |
| Model usage | N/A (no AI) | Gateway tiering: quality/vision for fraud, cheap for inventory lookup, opus for synthesis |
| Decision traceability | Audit log of rule outcomes | Full structured output from each specialist lane with reasoning field |
| Human approval | Separate manual queue tool | Native `PENDING_APPROVAL` state with approve/reject API + Temporal resumption |
| Memory | None (stateless per request) | Vector-store behavioral memory (Milvus) across all customer interactions |
| Inventory writes | Webhooks to WMS (async, brittle) | MCP tool call inside the Ops agent lane — synchronous, traceable |
| Extensibility | Config UI | Add an agent lane or MCP tool; the framework wires it |

### The core architectural difference

Existing platforms are **workflow engines with fixed decision trees**. You configure policies (return window, condition rules, fraud thresholds) and the system routes through them sequentially.

This engine is a **multi-agent reasoning system**: four specialist agents run *in parallel*, each on the model tier best suited to its task, and a synthesis agent merges their structured outputs into a single decision. The decision is not a lookup — it's an LLM reasoning step over four typed data structures. That means it degrades gracefully (each lane has a deterministic fallback), handles ambiguity, and can incorporate new signals just by adding a lane.

---

## Feature Backlog

### Tier 1 — High impact, buildable in days

**1. Return reason taxonomy + trend clustering**
A background agent that batches `reason_text` from recent returns, clusters them with embeddings, and surfaces "we've had 40 'zipper broke' returns on SKU p3 this week" — feeds directly into supplier escalation.

**2. Item grading pipeline**
After vision damage assessment, assign a resale grade (A/B/C/scrap) and route accordingly: A→store shelf, B→refurbishment partner, C→liquidation. The Ops lane already picks a store; add a branch for recommerce partners.

**3. Automatic exchange order creation**
When `exchange-offer` is accepted, call an OMS MCP tool to create a replacement order with the discount applied. Today the engine produces the offer message; it stops short of executing.

**4. Real carrier rate shopping**
The Ops lane generates a label stub. Wire a real carrier MCP (UPS/FedEx/EasyPost API) to compare live rates and pick the cheapest that meets the ETA constraint.

**5. Webhook / notification dispatch**
On `flag-for-review`, fire a Slack/email webhook with the fraud reasoning so the ops team gets an alert without polling the API. A `NotificationAgent` lane or a post-branch hook.

---

### Tier 2 — Meaningful, slightly more effort

**6. Proactive return prediction**
A scheduled agent scores all recent orders for return likelihood (based on return-reason history, product category, customer tier) and flags high-risk ones for proactive outreach before the customer initiates. Uses the same memory + demand data already seeded.

**7. Carbon footprint scoring per route decision**
Compute estimated CO₂ per route option (reroute-to-store vs. central warehouse vs. recommerce). Surface it in `ReturnResult` and let the synthesis agent factor it into the decision when savings are close — differentiator for sustainability-focused brands.

**8. Multi-language customer messages**
Add a `locale` field to `ReturnRequest`. The branch agents write the customer message in the detected locale. Since these are LLM outputs, cost is near-zero.

**9. Return window + policy engine per SKU category**
A lightweight rules layer (JSON config or a `PolicyAgent`) that gates requests before the lanes run: "electronics → 15-day window", "apparel → 30-day, no receipt OK". Currently hard-coded to accept everything.

**10. A/B testing framework for exchange incentives**
Route a percentage of `exchange-offer` decisions through a variant Marketing agent with different discount logic, track acceptance rate, and auto-promote the winner. Continuum's `RouterConfig(routing_strategy="rule_based")` can split traffic.

---

### Tier 3 — Larger scope

**11. Shopify / Magento integration**
MCP tools that pull order data from the commerce platform directly, so the API caller doesn't need to supply `item_name`, `order_value`, or `photo_base64` manually — the engine fetches them by `order_id`.

**12. Batch return processing**
Accept an array of `ReturnRequest` objects and fan them out with `asyncio.gather` across engine instances. Relevant for B2B / wholesale customers returning pallets.

**13. Analytics dashboard**
A lightweight time-series store (or just Postgres) that captures one row per `ReturnResult` — route, latency, asset_recovery_usd, fraud risk, LTV tier. A read endpoint + simple frontend gives ops the insight layer missing from the current API.

**14. Supplier chargeback automation**
When damage is detected and `consistent_with_reason=True`, generate a structured chargeback claim against the supplier with photo evidence and the vision model's `observations` field as the written finding.

**15. Temporal-native durable approval flow**
Today, approval state lives in-process (`_pending` dict). A Temporal workflow makes it durable: the engine suspends after flagging, an ops agent picks it up hours later, and the system resumes — survives restarts, scales horizontally.
