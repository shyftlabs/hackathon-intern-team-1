"""Web service + demo UI for the Returns Optimization Engine (Starlette).

(FastAPI isn't in the Continuum venv, but Starlette + uvicorn are — and that's
all we need.)

Run:
    ../continuum/.venv/bin/python -m uvicorn app.api:app --port 8099
    # then open http://localhost:8099

Endpoints:
    GET  /                       demo UI
    POST /return                 process a return  (body: ReturnRequest) -> ReturnResult
    POST /returns/{id}/approve   resume a flagged return (human approves)
    POST /returns/{id}/reject    deny a flagged return  (human rejects)
    GET  /pending                list returns awaiting approval
    GET  /samples                seed customers/products + sample photos for the UI
    GET  /health                 engine + subsystem status
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

import app.seed_data as seed_data
from app.config import APP_CONFIG
from app.engine import ReturnsEngine
from app.sample_image import SAMPLE_CLEAN_B64, SAMPLE_DAMAGED_B64
from app.schemas import ReturnRequest

_engine: ReturnsEngine | None = None
_init_error: str | None = None


@asynccontextmanager
async def lifespan(_: Starlette):
    global _engine, _init_error
    _engine = ReturnsEngine()
    try:
        await _engine.initialize()
    except Exception as exc:  # surface init errors via /health instead of crashing
        _init_error = str(exc)
    yield
    if _engine:
        await _engine.close()


def _engine_or_503() -> ReturnsEngine | JSONResponse:
    if _engine is None or not _engine._initialized:
        return JSONResponse({"error": f"Engine not ready: {_init_error or 'initializing'}"}, 503)
    return _engine


async def _reviewer(request: Request) -> str:
    try:
        body = await request.json()
        return (body or {}).get("reviewer", "ops-team")
    except Exception:
        return "ops-team"


async def index(_: Request) -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


async def process_return(request: Request) -> JSONResponse:
    eng = _engine_or_503()
    if isinstance(eng, JSONResponse):
        return eng
    try:
        req = ReturnRequest(**(await request.json()))
    except (ValidationError, ValueError, TypeError) as exc:
        return JSONResponse({"error": f"Invalid request: {exc}"}, 422)
    result = await eng.process_return(req)
    return JSONResponse(result.model_dump())


async def _resolve(request: Request, approved: bool) -> JSONResponse:
    eng = _engine_or_503()
    if isinstance(eng, JSONResponse):
        return eng
    return_id = request.path_params["return_id"]
    try:
        result = await eng.resolve_approval(return_id, approved=approved, reviewer=await _reviewer(request))
        return JSONResponse(result.model_dump())
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, 404)


async def approve(request: Request) -> JSONResponse:
    return await _resolve(request, approved=True)


async def reject(request: Request) -> JSONResponse:
    return await _resolve(request, approved=False)


async def pending(_: Request) -> JSONResponse:
    eng = _engine_or_503()
    if isinstance(eng, JSONResponse):
        return eng
    return JSONResponse({"pending": eng.pending_ids()})


async def samples(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "customers": [
                {"id": cid, **{k: c[k] for k in ("name", "ltv_tier", "fraud_flags", "return_count")}}
                for cid, c in seed_data.CUSTOMERS.items()
            ],
            "products": [{"sku": s, **p} for s, p in seed_data.PRODUCTS.items()],
            "sample_clean_b64": SAMPLE_CLEAN_B64,
            "sample_damaged_b64": SAMPLE_DAMAGED_B64,
        }
    )


async def health(_: Request) -> JSONResponse:
    ready = _engine is not None and _engine._initialized
    return JSONResponse(
        {
            "ready": ready,
            "init_error": _init_error,
            "gateway_active": APP_CONFIG.gateway_active,
            "memory_enabled": getattr(_engine, "memory_enabled", False) if _engine else False,
            "pending_approvals": _engine.pending_ids() if ready else [],
        }
    )


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", index, methods=["GET"]),
        Route("/return", process_return, methods=["POST"]),
        Route("/returns/{return_id}/approve", approve, methods=["POST"]),
        Route("/returns/{return_id}/reject", reject, methods=["POST"]),
        Route("/pending", pending, methods=["GET"]),
        Route("/samples", samples, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
    ],
)


# ---------------------------------------------------------------------------
# Single-page demo UI
# ---------------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Returns Optimization Engine · Continuum</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#26303d; --ink:#e6edf3; --mut:#8b97a6;
          --acc:#7c6cf0; --green:#1f9e6e; --amber:#c98a14; --red:#d8453f; --blue:#1f78c9; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         background:var(--bg); color:var(--ink); }
  header { padding:14px 22px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:12px; }
  header h1 { font-size:16px; margin:0; font-weight:650; }
  header .sub { color:var(--mut); font-size:12.5px; }
  header .pill { margin-left:auto; font-size:11.5px; color:var(--mut); border:1px solid var(--line);
                 padding:4px 10px; border-radius:20px; }
  .wrap { display:grid; grid-template-columns:380px 1fr; gap:18px; padding:18px 22px; align-items:start; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px; }
  label { display:block; font-size:12px; color:var(--mut); margin:10px 0 4px; }
  select,textarea,input[type=text]{ width:100%; background:#0d1117; color:var(--ink);
        border:1px solid var(--line); border-radius:8px; padding:9px 10px; font-size:13px; }
  textarea { resize:vertical; min-height:60px; }
  .row { display:flex; gap:8px; } .row > * { flex:1; }
  .chips { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:6px; }
  .chip { font-size:11.5px; padding:5px 10px; border-radius:14px; border:1px solid var(--line);
          background:#0d1117; color:var(--mut); cursor:pointer; }
  .chip:hover { border-color:var(--acc); color:var(--ink); }
  button.go { margin-top:14px; width:100%; background:var(--acc); color:white; border:none;
              border-radius:9px; padding:11px; font-size:14px; font-weight:600; cursor:pointer; }
  button.go:disabled { opacity:.5; cursor:wait; }
  .thumb { margin-top:8px; height:64px; border:1px solid var(--line); border-radius:8px; object-fit:contain;
           background:#0d1117; display:none; }
  .empty { color:var(--mut); font-size:13px; padding:40px; text-align:center; }
  .badge { display:inline-block; font-size:11.5px; font-weight:700; padding:4px 12px; border-radius:20px;
           text-transform:uppercase; letter-spacing:.4px; }
  .b-reroute{ background:rgba(31,158,110,.16); color:#54d39e; border:1px solid rgba(31,158,110,.4); }
  .b-exchange{ background:rgba(201,138,20,.16); color:#e6b257; border:1px solid rgba(201,138,20,.4); }
  .b-flag{ background:rgba(216,69,63,.16); color:#f0817c; border:1px solid rgba(216,69,63,.4); }
  .head { display:flex; align-items:center; gap:12px; margin-bottom:4px; }
  .headline { font-size:15px; font-weight:600; margin:8px 0 2px; }
  .msg { background:#0d1117; border:1px solid var(--line); border-radius:8px; padding:11px 13px;
         font-size:13px; line-height:1.5; margin:10px 0; }
  .grid4 { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:12px; }
  .lane { background:#0d1117; border:1px solid var(--line); border-radius:10px; padding:11px; }
  .lane h4 { margin:0 0 6px; font-size:12px; display:flex; align-items:center; gap:6px; }
  .lane .tier { font-size:10px; color:var(--mut); font-weight:500; border:1px solid var(--line);
                padding:1px 6px; border-radius:10px; margin-left:auto; }
  .kv { font-size:12px; color:var(--mut); line-height:1.6; }
  .kv b { color:var(--ink); font-weight:600; }
  .meter { height:6px; background:#26303d; border-radius:4px; overflow:hidden; margin:5px 0; }
  .meter > span { display:block; height:100%; }
  .foot { display:flex; gap:14px; flex-wrap:wrap; margin-top:12px; font-size:12px; color:var(--mut);
          align-items:center; }
  .foot a { color:var(--blue); text-decoration:none; }
  .approve-bar { margin-top:12px; padding:12px; border:1px dashed var(--amber); border-radius:10px;
                 background:rgba(201,138,20,.07); }
  .approve-bar .btns { display:flex; gap:8px; margin-top:8px; }
  .approve-bar button { flex:1; border:none; border-radius:8px; padding:9px; font-weight:600; cursor:pointer; }
  .ok { background:var(--green); color:white; } .no { background:var(--red); color:white; }
  .recovery { font-size:22px; font-weight:700; color:#54d39e; }
  details { margin-top:10px; } summary { cursor:pointer; color:var(--mut); font-size:12px; }
  pre { background:#0d1117; border:1px solid var(--line); border-radius:8px; padding:10px;
        font-size:11px; overflow:auto; max-height:240px; color:#a8c7e8; }
</style>
</head>
<body>
<header>
  <span style="font-size:20px">♻️</span>
  <div>
    <h1>Automated Omnichannel Returns Optimization Engine</h1>
    <div class="sub">Continuum · 4 parallel agents on different gateway tiers → synthesis → router branch</div>
  </div>
  <span class="pill" id="health">checking…</span>
</header>

<div class="wrap">
  <div class="card">
    <div style="font-weight:600;margin-bottom:6px;font-size:13px">New return request</div>
    <div class="chips" id="scenarios">
      <span class="chip" onclick="loadScenario('alpha')">▶ Platinum · genuine</span>
      <span class="chip" onclick="loadScenario('walkin')">▶ Walk-in · refund</span>
      <span class="chip" onclick="loadScenario('frank')">▶ Serial returner · suspicious</span>
    </div>

    <label>Customer</label>
    <select id="customer"></select>

    <div class="row">
      <div><label>Order ID</label><input id="order" type="text" value="ORD-9001" /></div>
      <div><label>Customer ZIP</label><input id="zip" type="text" value="10001" /></div>
    </div>

    <label>Product (SKU)</label>
    <select id="sku"></select>

    <label>Return reason</label>
    <textarea id="reason">Item arrived damaged and won't power on.</textarea>

    <label>Product photo (optional)</label>
    <input id="photo" type="file" accept="image/*" />
    <div class="chips" style="margin-top:6px">
      <span class="chip" onclick="useSample('clean')">use sample: clean</span>
      <span class="chip" onclick="useSample('damaged')">use sample: damaged</span>
      <span class="chip" onclick="clearPhoto()">clear</span>
    </div>
    <img id="thumb" class="thumb" />

    <button class="go" id="go" onclick="submitReturn()">Process return</button>
  </div>

  <div class="card" id="result">
    <div class="empty">Submit a return to see the multi-agent decision.<br/>
      Fraud · Demand · Marketing · Ops run in parallel, each on its own model tier.</div>
  </div>
</div>

<script>
let SAMPLES = null, photoB64 = null;

async function init() {
  try {
    const h = await (await fetch('/health')).json();
    document.getElementById('health').textContent =
      `gateway:${h.gateway_active?'on':'off'} · memory:${h.memory_enabled?'on':'off'} · ${h.ready?'ready':'starting…'}`;
  } catch (e) {}
  SAMPLES = await (await fetch('/samples')).json();
  const cust = document.getElementById('customer');
  cust.innerHTML = SAMPLES.customers.map(c =>
    `<option value="${c.id}">${c.name} — ${c.ltv_tier}, ${c.fraud_flags} fraud flag(s), ${c.return_count} returns</option>`
  ).join('') + `<option value="cust_walkin">New walk-in customer (no history)</option>`;
  document.getElementById('sku').innerHTML = SAMPLES.products.map(p =>
    `<option value="${p.sku}">${p.name} — $${p.price}</option>`).join('');
}

function setPhoto(b64) {
  photoB64 = b64;
  const t = document.getElementById('thumb');
  if (b64) { t.src = 'data:image/png;base64,' + b64; t.style.display = 'block'; }
  else { t.style.display = 'none'; }
}
function useSample(kind){ setPhoto(kind==='damaged'?SAMPLES.sample_damaged_b64:SAMPLES.sample_clean_b64); }
function clearPhoto(){ setPhoto(null); document.getElementById('photo').value=''; }

document.addEventListener('change', e => {
  if (e.target.id === 'photo' && e.target.files[0]) {
    const r = new FileReader();
    r.onload = () => setPhoto(String(r.result).split(',')[1]);
    r.readAsDataURL(e.target.files[0]);
  }
});

const SCN = {
  alpha:  { customer:'cust_alpha', sku:'p2', zip:'10001', order:'ORD-5001',
            reason:"Jacket is a bit too warm for my climate, I'd prefer a lighter one.", photo:'clean' },
  walkin: { customer:'cust_walkin', sku:'p1', zip:'07302', order:'ORD-5002',
            reason:"Boots are fine but I changed my mind — I just want a refund, no exchange.", photo:'clean' },
  frank:  { customer:'cust_frank', sku:'p3', zip:'07302', order:'ORD-5003',
            reason:"Earbuds arrived defective and won't charge.", photo:'damaged' },
};
function loadScenario(k){
  const s = SCN[k];
  document.getElementById('customer').value = s.customer;
  document.getElementById('sku').value = s.sku;
  document.getElementById('zip').value = s.zip;
  document.getElementById('order').value = s.order;
  document.getElementById('reason').value = s.reason;
  useSample(s.photo);
}

function bar(v, color){ return `<div class="meter"><span style="width:${Math.round(v*100)}%;background:${color}"></span></div>`; }

function render(r) {
  const cls = {'reroute-label':'b-reroute','exchange-offer':'b-exchange','flag-for-review':'b-flag'}[r.route]||'b-reroute';
  const f=r.fraud||{}, d=r.demand||{}, m=r.incentive||{}, o=r.ops||{};
  let approve = '';
  if (r.status === 'PENDING_APPROVAL') {
    approve = `<div class="approve-bar"><b>⏸ Human-in-the-loop:</b> this return is paused for review (Continuum approval gate).
      <div class="btns">
        <button class="ok" onclick="resolve('${r.return_id}',true)">Approve & reroute</button>
        <button class="no" onclick="resolve('${r.return_id}',false)">Reject return</button>
      </div></div>`;
  }
  const artNames = (r.artifacts && r.artifacts.tool_artifacts)
      ? r.artifacts.tool_artifacts.map(a=>a.tool_name) : [];
  document.getElementById('result').innerHTML = `
    <div class="head"><span class="badge ${cls}">${r.route}</span>
      <span class="kv">${r.status}</span>
      <span class="recovery" style="margin-left:auto">$${r.asset_recovery_usd.toFixed(2)}<span class="kv" style="font-size:11px"> recovered</span></span>
    </div>
    <div class="headline">${r.headline}</div>
    <div class="msg">${esc(r.customer_message)}</div>
    ${approve}
    <div class="grid4">
      <div class="lane"><h4>🛡 Fraud<span class="tier">${tier(r,'fraud')}</span></h4>
        ${bar(f.risk_score||0, f.flag_for_review?'#d8453f':'#1f9e6e')}
        <div class="kv">risk <b>${(f.risk_score??0).toFixed(2)}</b> · damage <b>${f.damage_level||'-'}</b> · flag <b>${f.flag_for_review}</b><br/>${esc(f.flag_reason||f.observations||'')}</div></div>
      <div class="lane"><h4>📈 Demand<span class="tier">${tier(r,'demand')}</span></h4>
        <div class="kv">best store <b>${d.best_store_id||'-'}</b> · deficit <b>${(d.best_store_deficit??0).toFixed(2)}</b> · <b>${(d.distance_km??0)}km</b><br/>${esc(d.summary||'')}</div></div>
      <div class="lane"><h4>🎁 Marketing<span class="tier">${tier(r,'marketing')}</span></h4>
        <div class="kv">offer <b>${m.offer_incentive}</b> · <b>${m.offer_type||'-'}</b> ${(m.discount_pct||0)}% · ${m.channel||'-'}<br/>tier <b>${m.ltv_tier||'-'}</b></div></div>
      <div class="lane"><h4>🚚 Ops<span class="tier">${tier(r,'ops')}</span></h4>
        <div class="kv">→ <b>${o.target_store_id||'-'}</b> · ETA <b>${o.eta_days||0}d</b> · save <b>$${(o.estimated_savings_usd??0).toFixed(2)}</b><br/>label <b>${o.label_id||'-'}</b></div></div>
    </div>
    <div class="foot">
      <span>⚙ synthesis: ${tier(r,'synthesis')}</span>
      <span>⏱ ${r.latency_ms} ms</span>
      <span>🧠 memory: ${r.memory_used}</span>
      ${artNames.length?`<span>📦 MCP artifacts: ${artNames.join(', ')}</span>`:''}
      ${r.trace_url?`<a href="${r.trace_url}" target="_blank">🔎 Langfuse trace ↗</a>`:''}
    </div>
    <details><summary>reasoning + raw decision</summary>
      <div class="msg">${esc(r.reasoning)}</div>
      <pre>${esc(JSON.stringify(r, null, 2))}</pre></details>`;
}
function tier(r,k){ return (r.models_used&&r.models_used[k])?r.models_used[k].split('(')[0].trim():''; }
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function submitReturn(){
  const go = document.getElementById('go'); go.disabled = true; go.textContent = 'Running 4 agents in parallel…';
  document.getElementById('result').innerHTML = '<div class="empty">⏳ Fraud · Demand · Marketing · Ops running concurrently, then synthesis + routing…</div>';
  const body = {
    customer_id: document.getElementById('customer').value,
    order_id: document.getElementById('order').value,
    sku: document.getElementById('sku').value,
    reason_text: document.getElementById('reason').value,
    customer_zip: document.getElementById('zip').value,
    photo_base64: photoB64,
  };
  try {
    const r = await (await fetch('/return',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    render(r);
  } catch(e){ document.getElementById('result').innerHTML = '<div class="empty">Error: '+e.message+'</div>'; }
  go.disabled = false; go.textContent = 'Process return';
}
async function resolve(id, approved){
  const url = `/returns/${id}/${approved?'approve':'reject'}`;
  const r = await (await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
  render(r);
}
init();
</script>
</body>
</html>
"""
