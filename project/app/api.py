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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>Returns Optimization Engine · Continuum</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#07080c;
    --s1:#0d0f17;
    --s2:#13151e;
    --s3:#191c28;
    --hair:rgba(255,255,255,.062);
    --hair2:rgba(255,255,255,.11);
    --ink:#ecedf7;
    --ink2:#8892ac;
    --ink3:#50596e;
    --brand:#818cf8;
    --brand2:#a5b4fc;
    --brand-soft:rgba(129,140,248,.12);
    --brand-glow:rgba(129,140,248,.26);
    --emerald:#34d399;
    --emerald-soft:rgba(52,211,153,.1);
    --amber:#fbbf24;
    --amber-soft:rgba(251,191,36,.1);
    --rose:#fb7185;
    --rose-soft:rgba(251,113,133,.1);
    --sky:#7dd3fc;
    --sky-soft:rgba(125,211,252,.1);
    --rs:7px;--rm:11px;--rl:15px;--rx:22px;
    --sh1:0 1px 0 rgba(255,255,255,.04) inset,0 1px 3px rgba(0,0,0,.45);
    --sh2:0 1px 0 rgba(255,255,255,.04) inset,0 8px 28px -12px rgba(0,0,0,.6);
    --sans:'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
    --mono:'JetBrains Mono',ui-monospace,'SF Mono',Menlo,Consolas,monospace;
  }
  *,*::before,*::after{ box-sizing:border-box; }
  html{ -webkit-text-size-adjust:100%; }
  body{
    margin:0; min-height:100dvh; background:var(--bg); color:var(--ink);
    font-family:var(--sans); font-size:14.5px; line-height:1.55; letter-spacing:-.012em;
    -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
  }
  .bgfx{ position:fixed; inset:0; z-index:-1; pointer-events:none;
    background:
      radial-gradient(700px 380px at 88% -6%,rgba(129,140,248,.09),transparent 65%),
      radial-gradient(400px 260px at 4% 0%,rgba(52,211,153,.04),transparent 55%); }
  .bgfx::after{ content:''; position:absolute; inset:0;
    background-image:radial-gradient(rgba(255,255,255,.018) 1px,transparent 1px);
    background-size:28px 28px;
    -webkit-mask-image:linear-gradient(180deg,rgba(0,0,0,.6),transparent 42%);
    mask-image:linear-gradient(180deg,rgba(0,0,0,.6),transparent 42%); }
  svg{ display:block; }
  .vh{ position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
  :focus-visible{ outline:2px solid var(--brand); outline-offset:2px; border-radius:4px; }

  /* ---------- top bar ---------- */
  .topbar{ position:sticky; top:0; z-index:50; display:flex; align-items:center; gap:14px;
    padding:12px 24px; border-bottom:1px solid var(--hair);
    background:rgba(7,8,12,.8); -webkit-backdrop-filter:blur(18px) saturate(160%);
    backdrop-filter:blur(18px) saturate(160%); }
  .brand{ display:flex; align-items:center; gap:11px; min-width:0; flex:1 1 auto; }
  .brand>div{ min-width:0; }
  .brand .title{ overflow:hidden; text-overflow:ellipsis; }
  .logo{ width:34px; height:34px; border-radius:10px; flex:none; display:grid; place-items:center;
    color:var(--brand2); background:linear-gradient(155deg,#252848,#11121a);
    border:1px solid var(--hair2); box-shadow:var(--sh1); }
  .brand .kicker{ font:600 9.5px/1 var(--mono); letter-spacing:.22em; text-transform:uppercase;
    color:var(--ink3); margin-bottom:4px; }
  .brand .title{ font-size:14.5px; font-weight:600; letter-spacing:-.015em; white-space:nowrap; }
  .statuses{ margin-left:auto; display:flex; gap:7px; flex-wrap:wrap; justify-content:flex-end; }
  .pill{ display:inline-flex; align-items:center; gap:7px; padding:5px 10px; border-radius:999px;
    background:var(--s2); border:1px solid var(--hair); font:500 11.5px/1 var(--sans); color:var(--ink3); }
  .pill .dot{ width:6px; height:6px; border-radius:50%; background:var(--ink3); flex:none; }
  .pill .dot.on{ background:var(--emerald); }
  .pill .dot.live{ animation:breathe 2.4s ease-in-out infinite; }
  .pill .dot.off{ background:var(--rose); }
  .pill b{ color:var(--ink2); font:600 11px/1 var(--mono); }

  /* ---------- layout ---------- */
  .work{ display:grid; grid-template-columns:384px 1fr; gap:20px;
    max-width:1340px; margin:0 auto; padding:24px 24px 72px; align-items:start; }
  @media(max-width:980px){ .work{ grid-template-columns:1fr; padding:20px 16px 56px; } }
  @media(max-width:560px){
    .topbar{ flex-wrap:wrap; gap:10px; }
    .statuses{ margin-left:0; width:100%; justify-content:flex-start; }
  }

  /* ---------- control panel ---------- */
  .panel{ position:sticky; top:80px; background:var(--s1); border:1px solid var(--hair);
    border-radius:var(--rl); padding:18px; box-shadow:var(--sh2); }
  @media(max-width:980px){ .panel{ position:static; } }
  .phead{ display:flex; align-items:center; gap:9px; }
  .phead .pic{ width:26px; height:26px; border-radius:7px; display:grid; place-items:center;
    color:var(--brand2); background:var(--brand-soft); border:1px solid var(--hair); flex:none; }
  .phead h2{ margin:0; font-size:13.5px; font-weight:600; letter-spacing:-.012em; }
  .psub{ margin:8px 0 16px; font-size:12.5px; line-height:1.55; color:var(--ink3); }

  .glabel{ font:500 10px/1 var(--mono); letter-spacing:.15em; text-transform:uppercase;
    color:var(--ink3); display:block; margin:0 0 8px; }
  .scenarios{ display:grid; gap:7px; margin-bottom:18px; }
  .scn{ display:flex; align-items:center; gap:11px; width:100%; text-align:left; cursor:pointer;
    background:var(--s2); border:1px solid var(--hair); border-radius:var(--rm); padding:10px 11px;
    color:var(--ink); transition:transform .14s ease,border-color .14s ease,background .14s ease; }
  .scn:hover{ background:var(--s3); border-color:var(--hair2); transform:translateY(-1px); }
  .scn[aria-pressed="true"]{ border-color:var(--brand); box-shadow:0 0 0 1px var(--brand) inset; }
  .scn-ic{ width:30px; height:30px; border-radius:8px; flex:none; display:grid; place-items:center;
    border:1px solid var(--hair); }
  .scn-ic.exchange{ color:var(--amber); background:var(--amber-soft); }
  .scn-ic.reroute{ color:var(--emerald); background:var(--emerald-soft); }
  .scn-ic.flag{ color:var(--rose); background:var(--rose-soft); }
  .scn-tx{ min-width:0; display:flex; flex-direction:column; gap:2px; }
  .scn-t{ display:block; font-size:12px; font-weight:600; letter-spacing:-.01em; }
  .scn-d{ display:block; font-size:11px; color:var(--ink3); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

  .field{ margin-bottom:13px; }
  .field>label{ font:500 10px/1 var(--mono); letter-spacing:.14em; text-transform:uppercase;
    color:var(--ink3); display:block; margin-bottom:7px; }
  .control{ width:100%; background:var(--s2); color:var(--ink); border:1px solid var(--hair);
    border-radius:var(--rs); padding:9px 11px; font-size:13px; font-family:var(--sans);
    transition:border-color .14s ease,box-shadow .14s ease; }
  .control::placeholder{ color:var(--ink3); }
  .control:hover{ border-color:var(--hair2); }
  .control:focus{ outline:none; border-color:var(--brand); box-shadow:0 0 0 3px rgba(129,140,248,.15); }
  select.control{ appearance:none; -webkit-appearance:none; cursor:pointer; padding-right:34px;
    background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%2350596e' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>");
    background-repeat:no-repeat; background-position:right 10px center; }
  textarea.control{ resize:vertical; min-height:70px; line-height:1.55; }
  .row2{ display:grid; grid-template-columns:1fr 1fr; gap:9px; }

  .photo-row{ display:flex; flex-wrap:wrap; gap:7px; }
  .chip{ display:inline-flex; align-items:center; gap:6px; cursor:pointer; font:500 11.5px/1 var(--sans);
    color:var(--ink2); background:var(--s2); border:1px solid var(--hair); border-radius:999px; padding:7px 11px;
    transition:border-color .14s ease,color .14s ease,background .14s ease; }
  .chip:hover{ border-color:var(--brand); color:var(--ink); }
  .chip svg{ color:var(--ink3); }
  .chip:hover svg{ color:var(--brand2); }
  .thumb-wrap{ display:none; margin-top:9px; position:relative; }
  .thumb{ width:100%; height:120px; object-fit:cover; border-radius:var(--rm);
    border:1px solid var(--hair); background:var(--s2); }
  .thumb-tag{ position:absolute; left:9px; bottom:9px; font:500 10px/1 var(--mono);
    letter-spacing:.07em; text-transform:uppercase; color:var(--ink); padding:4px 7px; border-radius:5px;
    background:rgba(7,8,12,.72); border:1px solid var(--hair2); -webkit-backdrop-filter:blur(6px); backdrop-filter:blur(6px); }

  .cta{ margin-top:5px; width:100%; display:inline-flex; align-items:center; justify-content:center; gap:8px;
    border:none; cursor:pointer; color:#fff; font:600 13.5px/1 var(--sans); letter-spacing:-.01em;
    padding:12px; border-radius:var(--rm); background:linear-gradient(180deg,#6366f1,#4f46e5);
    box-shadow:0 1px 0 rgba(255,255,255,.16) inset,0 4px 14px -6px var(--brand-glow);
    transition:filter .14s ease,transform .12s ease,box-shadow .14s ease; }
  .cta:hover{ filter:brightness(1.1); }
  .cta:active{ transform:translateY(1px); filter:brightness(.94); }
  .cta:disabled{ cursor:wait; filter:saturate(.4) brightness(.7); box-shadow:none; }

  /* ---------- canvas / empty ---------- */
  .canvas{ min-height:560px; }
  .empty{ display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center;
    min-height:560px; padding:56px 32px; border:1px solid var(--hair); border-radius:var(--rl);
    background:radial-gradient(500px 240px at 50% -4%,rgba(129,140,248,.05),transparent 65%),var(--s1);
    box-shadow:var(--sh2); }
  .empty-ic{ width:60px; height:60px; border-radius:17px; display:grid; place-items:center; color:var(--brand2);
    background:var(--brand-soft); border:1px solid rgba(129,140,248,.2); }
  .empty h3{ margin:18px 0 7px; font-size:18px; font-weight:600; letter-spacing:-.02em; }
  .empty p{ margin:0 0 24px; max-width:44ch; font-size:13px; line-height:1.65; color:var(--ink3); }
  .miniflow{ display:flex; align-items:center; gap:7px; flex-wrap:wrap; justify-content:center; }
  .fnode{ font:500 11px/1 var(--mono); letter-spacing:.02em; color:var(--ink2); background:var(--s2);
    border:1px solid var(--hair); border-radius:999px; padding:7px 11px; }
  .farr{ color:var(--ink3); }

  /* ---------- pipeline rail ---------- */
  .rail{ display:flex; align-items:center; gap:0; overflow-x:auto; scrollbar-width:none;
    padding:13px 16px; margin-bottom:16px; background:var(--s1); border:1px solid var(--hair);
    border-radius:var(--rl); box-shadow:var(--sh1); }
  .rail::-webkit-scrollbar{ display:none; }
  .step{ display:flex; align-items:center; gap:9px; flex:none; }
  .step .node{ width:28px; height:28px; border-radius:8px; flex:none; display:grid; place-items:center;
    color:var(--ink3); background:var(--s3); border:1px solid var(--hair); transition:all .2s ease; }
  .step.active .node{ color:var(--brand2); background:var(--brand-soft); border-color:var(--brand); animation:breathe 1.8s ease-in-out infinite; }
  .step.done .node{ color:var(--emerald); background:var(--emerald-soft); border-color:rgba(52,211,153,.38); }
  .step .stx .s1{ font-size:12px; font-weight:600; letter-spacing:-.01em; }
  .step .stx .s2{ font:500 9px/1.3 var(--mono); letter-spacing:.08em; text-transform:uppercase; color:var(--ink3); margin-top:3px; }
  .step.active .stx .s2{ color:var(--brand2); }
  .step.done .stx .s2{ color:var(--emerald); }
  .connector{ width:36px; height:1px; margin:0 12px; flex:none; border-radius:2px;
    background:var(--hair); transition:background .3s ease; }
  .connector.done{ background:rgba(52,211,153,.36); }
  @media(max-width:560px){ .step .stx{ display:none; } .connector{ width:16px; margin:0 7px; } }

  /* ---------- decision ---------- */
  .decision{ position:relative; overflow:hidden; padding:20px; margin-bottom:16px;
    background:var(--s1); border:1px solid var(--hair); border-radius:var(--rl); box-shadow:var(--sh2); }
  .decision::before{ content:''; position:absolute; left:0; top:0; bottom:0; width:3px; background:var(--ink3); }
  .decision.reroute::before{ background:var(--emerald); }
  .decision.exchange::before{ background:var(--amber); }
  .decision.flag::before{ background:var(--rose); }
  .dtop{ display:flex; align-items:flex-start; gap:12px; flex-wrap:wrap; }
  .badge{ display:inline-flex; align-items:center; gap:7px; padding:6px 12px; border-radius:999px;
    font:600 11px/1 var(--mono); letter-spacing:.07em; text-transform:uppercase; border:1px solid; }
  .b-reroute{ color:var(--emerald); background:var(--emerald-soft); border-color:rgba(52,211,153,.3); }
  .b-exchange{ color:var(--amber); background:var(--amber-soft); border-color:rgba(251,191,36,.3); }
  .b-flag{ color:var(--rose); background:var(--rose-soft); border-color:rgba(251,113,133,.3); }
  .status-tag{ display:inline-flex; align-items:center; gap:6px; font:500 10.5px/1 var(--mono);
    letter-spacing:.06em; color:var(--ink2); padding:6px 10px; border:1px solid var(--hair);
    border-radius:999px; background:var(--s2); }
  .hero{ margin-left:auto; text-align:right; }
  .hero .v{ font:600 34px/1 var(--mono); letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
  .hero.reroute .v{ color:var(--emerald); } .hero.exchange .v{ color:var(--amber); } .hero.flag .v{ color:var(--rose); }
  .hero .l{ margin-top:6px; font:500 9.5px/1 var(--mono); letter-spacing:.13em; text-transform:uppercase; color:var(--ink3); }
  .headline{ margin:16px 0 0; font-size:18px; font-weight:600; line-height:1.38; letter-spacing:-.018em; }
  .msg{ margin-top:12px; padding:13px 15px; background:var(--s2); border:1px solid var(--hair);
    border-left:2px solid var(--brand); border-radius:var(--rm); font-size:13.5px; line-height:1.6; color:var(--ink); }
  .msg .who{ display:flex; align-items:center; gap:6px; margin-bottom:8px; color:var(--ink3);
    font:500 9.5px/1 var(--mono); letter-spacing:.12em; text-transform:uppercase; }

  .approval{ margin-top:14px; padding:14px; border:1px solid rgba(251,191,36,.35); border-radius:var(--rm);
    background:linear-gradient(180deg,rgba(251,191,36,.07),rgba(251,191,36,.02)); }
  .approval .at{ display:flex; align-items:center; gap:8px; font-size:13px; font-weight:600; color:var(--amber); }
  .approval p{ margin:8px 0 13px; font-size:12.5px; line-height:1.55; color:var(--ink2); }
  .approval .btns{ display:flex; gap:9px; }
  .btn{ flex:1; display:inline-flex; align-items:center; justify-content:center; gap:7px; cursor:pointer;
    font:600 13px/1 var(--sans); padding:10px; border-radius:var(--rs); border:1px solid var(--hair);
    background:var(--s2); color:var(--ink); transition:all .14s ease; }
  .btn:hover{ border-color:var(--hair2); }
  .btn.ok{ color:var(--emerald); background:var(--emerald-soft); border-color:rgba(52,211,153,.35); }
  .btn.ok:hover{ background:rgba(52,211,153,.18); }
  .btn.no{ color:var(--rose); background:var(--rose-soft); border-color:rgba(251,113,133,.3); }
  .btn.no:hover{ background:rgba(251,113,133,.18); }
  .btn:disabled{ opacity:.5; cursor:wait; }

  /* ---------- lanes ---------- */
  .lanes-h{ display:flex; align-items:baseline; gap:9px; margin:0 0 10px; }
  .lanes-h .t{ font-size:12.5px; font-weight:600; }
  .lanes-h .n{ font:500 10.5px/1 var(--mono); letter-spacing:.06em; color:var(--ink3); }
  .lanes{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  @media(max-width:560px){ .lanes{ grid-template-columns:1fr; } }
  .lane{ position:relative; padding:14px; background:var(--s1); border:1px solid var(--hair);
    border-radius:var(--rm); box-shadow:var(--sh1);
    transition:opacity .35s ease,transform .35s ease,border-color .2s ease; }
  .lane.enter{ opacity:0; transform:translateY(10px); }
  .lhead{ display:flex; align-items:center; gap:9px; margin-bottom:12px; }
  .lic{ width:32px; height:32px; border-radius:9px; flex:none; display:grid; place-items:center; border:1px solid var(--hair); }
  .lic.fraud{ color:var(--rose); background:var(--rose-soft); }
  .lic.demand{ color:var(--sky); background:var(--sky-soft); }
  .lic.mktg{ color:var(--amber); background:var(--amber-soft); }
  .lic.ops{ color:var(--emerald); background:var(--emerald-soft); }
  .lname{ font-size:12.5px; font-weight:600; letter-spacing:-.01em; }
  .lstatus{ display:flex; align-items:center; gap:5px; margin-top:2px; font:500 9.5px/1 var(--mono); letter-spacing:.05em; color:var(--ink3); }
  .lstatus .d{ width:5px; height:5px; border-radius:50%; background:var(--emerald); }
  .lstatus.run .d{ background:var(--brand2); animation:breathe 1.4s ease-in-out infinite; }
  .ltier{ margin-left:auto; text-align:right; }
  .tbadge{ display:inline-block; font:600 9px/1 var(--mono); letter-spacing:.08em; text-transform:uppercase;
    color:var(--ink2); background:var(--s3); border:1px solid var(--hair); padding:3px 6px; border-radius:5px; }
  .tres{ margin-top:4px; font:500 9px/1 var(--mono); color:var(--brand2); }
  .kv{ font-size:12px; line-height:1.75; color:var(--ink2); }
  .kv b{ color:var(--ink); font:600 11.5px/1.6 var(--mono); font-variant-numeric:tabular-nums; }
  .kv .sep{ color:var(--ink3); margin:0 5px; }
  .note{ display:block; margin-top:7px; padding-top:7px; border-top:1px solid var(--hair);
    font-size:11.5px; line-height:1.55; color:var(--ink3); }
  .meter{ height:5px; margin:3px 0 10px; background:var(--s3); border-radius:99px; overflow:hidden; }
  .meter>i{ display:block; height:100%; width:0; border-radius:99px; transition:width .75s cubic-bezier(.2,.8,.2,1); }
  .skel{ height:10px; border-radius:5px; margin:8px 0;
    background:linear-gradient(90deg,var(--s3) 25%,#21253a 50%,var(--s3) 75%); background-size:360px 100%;
    animation:shimmer 1.4s infinite linear; }

  /* ---------- meta footer ---------- */
  .meta{ display:flex; flex-wrap:wrap; gap:7px; margin-top:14px; }
  .m{ display:inline-flex; align-items:center; gap:7px; font:500 11px/1 var(--sans); color:var(--ink2);
    background:var(--s1); border:1px solid var(--hair); border-radius:999px; padding:7px 11px; }
  .m svg{ color:var(--ink3); }
  .m b{ color:var(--ink); font:600 10.5px/1 var(--mono); }
  a.m{ color:var(--sky); text-decoration:none; transition:border-color .14s ease; }
  a.m svg{ color:var(--sky); }
  a.m:hover{ border-color:var(--sky); }

  /* ---------- reasoning ---------- */
  details.reason{ margin-top:12px; background:var(--s1); border:1px solid var(--hair); border-radius:var(--rm); overflow:hidden; }
  details.reason>summary{ list-style:none; cursor:pointer; padding:11px 14px; display:flex; align-items:center; gap:8px;
    font-size:12px; font-weight:600; color:var(--ink2); }
  details.reason>summary::-webkit-details-marker{ display:none; }
  details.reason>summary .chev{ margin-left:auto; transition:transform .2s ease; color:var(--ink3); }
  details.reason[open]>summary{ border-bottom:1px solid var(--hair); }
  details.reason[open]>summary .chev{ transform:rotate(180deg); }
  .rbody{ padding:14px; }
  .rbody .txt{ font-size:12.5px; line-height:1.65; color:var(--ink2); }
  pre{ margin:12px 0 0; padding:12px; background:#090b10; border:1px solid var(--hair); border-radius:var(--rs);
    font:400 11px/1.65 var(--mono); color:#8fa3c0; overflow:auto; max-height:280px; }

  .errbox{ display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center;
    min-height:560px; padding:48px; border:1px solid rgba(251,113,133,.25); border-radius:var(--rl);
    background:var(--s1); color:var(--ink2); }
  .errbox .eic{ width:52px; height:52px; border-radius:14px; display:grid; place-items:center; color:var(--rose);
    background:var(--rose-soft); border:1px solid rgba(251,113,133,.3); margin-bottom:14px; }

  @keyframes breathe{ 0%,100%{ box-shadow:0 0 0 0 var(--brand-glow); } 50%{ box-shadow:0 0 0 4px transparent; } }
  @keyframes shimmer{ 0%{ background-position:-180px 0; } 100%{ background-position:180px 0; } }
  @keyframes spin{ to{ transform:rotate(360deg); } }
  .spin{ animation:spin .8s linear infinite; }
  @media (prefers-reduced-motion: reduce){
    *{ animation-duration:.001ms !important; animation-iteration-count:1 !important; transition-duration:.001ms !important; scroll-behavior:auto !important; }
  }
</style>
</head>
<body>
<div class="bgfx"></div>

<header class="topbar">
  <div class="brand">
    <div class="logo" id="logo" aria-hidden="true"></div>
    <div>
      <div class="kicker">Continuum</div>
      <div class="title">Returns Optimization Engine</div>
    </div>
  </div>
  <div class="statuses" id="statuses" role="status" aria-live="polite">
    <span class="pill"><span class="dot"></span> connecting…</span>
  </div>
</header>

<main class="work">
  <aside class="panel">
    <div class="phead">
      <div class="pic" id="pic-new" aria-hidden="true"></div>
      <h2>New return request</h2>
    </div>
    <p class="psub">Four specialist agents evaluate the return in parallel, each on its own Smart-Gateway model tier, then a synthesis agent routes the outcome.</p>

    <span class="glabel">Demo scenarios</span>
    <div class="scenarios" id="scenarios"></div>

    <div class="field">
      <label for="customer">Customer</label>
      <select id="customer" class="control"></select>
    </div>

    <div class="row2">
      <div class="field"><label for="order">Order ID</label><input id="order" class="control" type="text" value="ORD-9001" /></div>
      <div class="field"><label for="zip">Customer ZIP</label><input id="zip" class="control" type="text" inputmode="numeric" value="10001" /></div>
    </div>

    <div class="field">
      <label for="sku">Product (SKU)</label>
      <select id="sku" class="control"></select>
    </div>

    <div class="field">
      <label for="reason">Return reason</label>
      <textarea id="reason" class="control">Item arrived damaged and won't power on.</textarea>
    </div>

    <div class="field">
      <label>Product photo · optional</label>
      <div class="photo-row">
        <label class="chip" for="photo" id="chip-upload" tabindex="0">Upload</label>
        <input id="photo" type="file" accept="image/*" class="vh" />
        <button class="chip" type="button" onclick="useSample('clean')" id="chip-clean">Sample · clean</button>
        <button class="chip" type="button" onclick="useSample('damaged')" id="chip-damaged">Sample · damaged</button>
        <button class="chip" type="button" onclick="clearPhoto()" id="chip-clear">Clear</button>
      </div>
      <div class="thumb-wrap" id="thumbWrap">
        <img id="thumb" class="thumb" alt="Product photo preview" />
        <span class="thumb-tag" id="thumbTag">attached</span>
      </div>
    </div>

    <button class="cta" id="go" onclick="submitReturn()">
      <span id="goic" aria-hidden="true"></span><span id="gotx">Process return</span>
    </button>
  </aside>

  <section class="canvas" id="canvas" aria-live="polite"></section>
</main>

<script>
const RM = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ---------- icon set (Lucide-style strokes) ---------- */
const I = {
  loop:'<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
  inbox:'<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  branch:'<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
  sparkles:'<path d="M12 3l1.7 4.6a2 2 0 0 0 1.2 1.2L19.5 10l-4.6 1.7a2 2 0 0 0-1.2 1.2L12 17.5l-1.7-4.6a2 2 0 0 0-1.2-1.2L4.5 10l4.6-1.7a2 2 0 0 0 1.2-1.2z"/>',
  route:'<circle cx="6" cy="19" r="3"/><circle cx="18" cy="5" r="3"/><path d="M9 19h6a3 3 0 0 0 3-3V8"/><path d="M6 16V9"/>',
  shield:'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  shieldAlert:'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
  trend:'<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
  tag:'<path d="M20.59 13.41 13.42 20.6a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
  truck:'<path d="M14 16V5a1 1 0 0 0-1-1H2a1 1 0 0 0-1 1v11h13z"/><path d="M14 9h4l4 4v3h-8z"/><circle cx="6" cy="18.5" r="2"/><circle cx="18.5" cy="18.5" r="2"/>',
  pin:'<path d="M21 10c0 7-9 12-9 12s-9-5-9-12a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
  db:'<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
  clock:'<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>',
  pkg:'<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22" x2="12" y2="12"/>',
  zap:'<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  link:'<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
  check:'<polyline points="20 6 9 17 4 12"/>',
  x:'<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  alert:'<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  msg:'<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  upload:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  chev:'<polyline points="6 9 12 15 18 9"/>',
  arrow:'<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
  layers:'<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
  cpu:'<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
};
function ic(name, size, cls){
  size = size || 16; cls = cls || '';
  return '<svg class="'+cls+'" width="'+size+'" height="'+size+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(I[name]||'')+'</svg>';
}

/* ---------- state ---------- */
let SAMPLES = null, photoB64 = null, activeScenario = null;
const $ = id => document.getElementById(id);

/* ---------- init ---------- */
async function init(){
  $('logo').innerHTML = ic('loop', 18);
  $('pic-new').innerHTML = ic('inbox', 16);
  $('goic').innerHTML = ic('zap', 16);
  renderEmpty();
  try{
    const h = await (await fetch('/health')).json();
    renderStatus(h);
  }catch(e){
    $('statuses').innerHTML = '<span class="pill"><span class="dot off"></span> offline</span>';
  }
  try{
    SAMPLES = await (await fetch('/samples')).json();
    fillSelects();
    renderScenarios();
  }catch(e){}
  // upload chip keyboard activation
  $('chip-upload').addEventListener('keydown', e => { if(e.key==='Enter'||e.key===' '){ e.preventDefault(); $('photo').click(); } });
}

function renderStatus(h){
  const ready = h.ready;
  $('statuses').innerHTML =
    pill('Gateway', h.gateway_active ? 'on':'off', h.gateway_active) +
    pill('Memory', h.memory_enabled ? 'on':'off', h.memory_enabled) +
    '<span class="pill"><span class="dot '+(ready?'on live':'')+'"></span><span>Engine</span> <b>'+(ready?'ready':'starting…')+'</b></span>';
}
function pill(label, val, on){
  return '<span class="pill"><span class="dot '+(on?'on':'off')+'"></span><span>'+label+'</span> <b>'+val+'</b></span>';
}

function fillSelects(){
  const cust = $('customer');
  cust.innerHTML = SAMPLES.customers.map(c =>
    '<option value="'+c.id+'">'+esc(c.name)+' — '+c.ltv_tier+', '+c.fraud_flags+' flag(s), '+c.return_count+' returns</option>'
  ).join('') + '<option value="cust_walkin">New walk-in customer (no history)</option>';
  $('sku').innerHTML = SAMPLES.products.map(p =>
    '<option value="'+p.sku+'">'+esc(p.name)+' — $'+p.price+'</option>').join('');
}

/* ---------- scenarios ---------- */
const SCN = {
  alpha:  { customer:'cust_alpha', sku:'p2', zip:'10001', order:'ORD-5001',
            reason:"Jacket is a bit too warm for my climate, I'd prefer a lighter one.", photo:'clean',
            t:'Platinum · genuine', d:'Loyal high-LTV buyer → exchange offer', ic:'tag', kind:'exchange' },
  walkin: { customer:'cust_walkin', sku:'p1', zip:'07302', order:'ORD-5002',
            reason:"Boots are fine but I changed my mind — I just want a refund, no exchange.", photo:'clean',
            t:'Walk-in · refund', d:'No history → reroute & recover logistics $', ic:'truck', kind:'reroute' },
  frank:  { customer:'cust_frank', sku:'p3', zip:'07302', order:'ORD-5003',
            reason:"Earbuds arrived defective and won't charge.", photo:'damaged',
            t:'Serial returner · suspicious', d:'Fraud history → human review gate', ic:'shieldAlert', kind:'flag' },
};
function renderScenarios(){
  $('scenarios').innerHTML = Object.keys(SCN).map(k => {
    const s = SCN[k];
    return '<button class="scn" type="button" aria-pressed="false" data-k="'+k+'" onclick="loadScenario(\''+k+'\')">'
      + '<span class="scn-ic '+s.kind+'">'+ic(s.ic,16)+'</span>'
      + '<span class="scn-tx"><span class="scn-t">'+s.t+'</span><span class="scn-d">'+s.d+'</span></span>'
      + '</button>';
  }).join('');
}
function loadScenario(k){
  const s = SCN[k];
  $('customer').value = s.customer; $('sku').value = s.sku; $('zip').value = s.zip;
  $('order').value = s.order; $('reason').value = s.reason;
  useSample(s.photo);
  activeScenario = k;
  document.querySelectorAll('.scn').forEach(b => b.setAttribute('aria-pressed', b.dataset.k===k ? 'true':'false'));
}

/* ---------- photo ---------- */
function setPhoto(b64, tag){
  photoB64 = b64;
  if(b64){ $('thumb').src = 'data:image/png;base64,'+b64; $('thumbWrap').style.display='block'; $('thumbTag').textContent = tag || 'attached'; }
  else { $('thumbWrap').style.display='none'; }
}
function useSample(kind){ setPhoto(kind==='damaged'?SAMPLES.sample_damaged_b64:SAMPLES.sample_clean_b64, 'sample · '+kind); }
function clearPhoto(){ setPhoto(null); $('photo').value=''; }
document.addEventListener('change', e => {
  if(e.target.id==='photo' && e.target.files[0]){
    const r = new FileReader();
    r.onload = () => setPhoto(String(r.result).split(',')[1], 'uploaded');
    r.readAsDataURL(e.target.files[0]);
  }
});

/* ---------- empty / running states ---------- */
function renderEmpty(){
  $('canvas').innerHTML =
    '<div class="empty">'
    + '<div class="empty-ic">'+ic('branch',28)+'</div>'
    + '<h3>Multi-agent return decision</h3>'
    + '<p>Pick a demo scenario and process a return. Fraud, Demand, Marketing and Ops run concurrently, each on the cheapest model tier that clears its bar, then synthesis selects one of three routes.</p>'
    + '<div class="miniflow">'
    + '<span class="fnode">Intake</span>'
    + '<span class="farr">'+ic('arrow',15)+'</span>'
    + '<span class="fnode">4 agents · parallel</span>'
    + '<span class="farr">'+ic('arrow',15)+'</span>'
    + '<span class="fnode">Synthesis</span>'
    + '<span class="farr">'+ic('arrow',15)+'</span>'
    + '<span class="fnode">Route</span>'
    + '</div></div>';
}

function railHTML(phase){
  // phase: 'analyze' | 'done'
  const steps = [
    {ic:'inbox', s1:'Intake', s2:'request'},
    {ic:'branch', s1:'Parallel analysis', s2:'4 agents'},
    {ic:'sparkles', s1:'Synthesis', s2:'merge'},
    {ic:'route', s1:'Route', s2:'decision'},
  ];
  const cls = phase==='done' ? ['done','done','done','done'] : ['done','active','',''];
  let h = '<div class="rail">';
  steps.forEach((st,i) => {
    h += '<div class="step '+cls[i]+'"><div class="node">'+ic(cls[i]==='done'?'check':st.ic,16)+'</div>'
       + '<div class="stx"><div class="s1">'+st.s1+'</div><div class="s2">'+st.s2+'</div></div></div>';
    if(i<steps.length-1) h += '<div class="connector '+(cls[i]==='done'&&cls[i+1]==='done'?'done':'')+'"></div>';
  });
  return h + '</div>';
}

function skelLane(name, icn, klass){
  return '<div class="lane"><div class="lhead"><div class="lic '+klass+'">'+ic(icn,17)+'</div>'
    + '<div><div class="lname">'+name+'</div><div class="lstatus run"><span class="d"></span>running</div></div></div>'
    + '<div class="skel" style="width:90%"></div><div class="skel" style="width:70%"></div><div class="skel" style="width:80%"></div></div>';
}
function renderRunning(){
  $('canvas').innerHTML = railHTML('analyze')
    + '<div class="lanes-h"><span class="t">Specialist agents</span><span class="n">running concurrently</span></div>'
    + '<div class="lanes">'
    + skelLane('Fraud','shield','fraud') + skelLane('Demand','trend','demand')
    + skelLane('Marketing','tag','mktg') + skelLane('Ops','truck','ops')
    + '</div>';
}

/* ---------- helpers ---------- */
function parseTier(str){
  if(!str) return {tier:'', resolved:''};
  const t = (str.match(/gateway_mode=(\w+)/)||[])[1] || '';
  const r = (str.match(/->\s*([\w\/]+)/)||[])[1] || '';
  return {tier:t, resolved:r};
}
function tierBadge(r, key){
  const p = parseTier(r.models_used && r.models_used[key]);
  if(!p.tier) return '';
  return '<div class="ltier"><span class="tbadge">'+p.tier+'</span>'+(p.resolved?'<div class="tres">→ '+p.resolved+'</div>':'')+'</div>';
}
function meter(frac, color){
  frac = Math.max(0, Math.min(1, frac||0));
  return '<div class="meter"><i data-w="'+Math.round(frac*100)+'" style="background:'+color+'"></i></div>';
}
function laneStatus(){ return '<div class="lstatus"><span class="d"></span>done</div>'; }

/* ---------- result render ---------- */
function resultHTML(r){
  const f = r.fraud||{}, d = r.demand||{}, m = r.incentive||{}, o = r.ops||{};
  const kind = {'reroute-label':'reroute','exchange-offer':'exchange','flag-for-review':'flag'}[r.route] || 'reroute';
  const badgeCls = {'reroute':'b-reroute','exchange':'b-exchange','flag':'b-flag'}[kind];
  const heroLabel = {'reroute':'logistics $ recovered','exchange':'revenue retained','flag':'value protected'}[kind];

  let approval = '';
  if(r.status === 'PENDING_APPROVAL'){
    approval = '<div class="approval"><div class="at">'+ic('alert',16)+'Human-in-the-loop · paused for review</div>'
      + '<p>This return tripped the fraud safeguard, so the Continuum approval gate held it before any refund. A reviewer decides whether to proceed.</p>'
      + '<div class="btns">'
      + '<button class="btn ok" onclick="resolve(\''+r.return_id+'\',true)">'+ic('check',16)+'Approve &amp; reroute</button>'
      + '<button class="btn no" onclick="resolve(\''+r.return_id+'\',false)">'+ic('x',16)+'Reject return</button>'
      + '</div></div>';
  }

  // lanes
  const fraudColor = f.flag_for_review ? 'var(--rose)' : 'var(--emerald)';
  const laneFraud = '<div class="lane enter"><div class="lhead"><div class="lic fraud">'+ic('shield',17)+'</div>'
    + '<div><div class="lname">Fraud</div>'+laneStatus()+'</div>'+tierBadge(r,'fraud')+'</div>'
    + meter(f.risk_score||0, fraudColor)
    + '<div class="kv">risk <b>'+num(f.risk_score)+'</b><span class="sep">·</span>damage <b>'+(f.damage_level||'—')+'</b><span class="sep">·</span>flag <b>'+(f.flag_for_review?'yes':'no')+'</b></div>'
    + note(f.flag_reason || f.observations) + '</div>';

  const laneDemand = '<div class="lane enter"><div class="lhead"><div class="lic demand">'+ic('trend',17)+'</div>'
    + '<div><div class="lname">Demand</div>'+laneStatus()+'</div>'+tierBadge(r,'demand')+'</div>'
    + meter(d.best_store_deficit||0, 'var(--sky)')
    + '<div class="kv">best store <b>'+(d.best_store_id||'—')+'</b><span class="sep">·</span>deficit <b>'+num(d.best_store_deficit)+'</b><span class="sep">·</span><b>'+fmtKm(d.distance_km)+'</b></div>'
    + note(d.summary) + '</div>';

  const laneMktg = '<div class="lane enter"><div class="lhead"><div class="lic mktg">'+ic('tag',17)+'</div>'
    + '<div><div class="lname">Marketing</div>'+laneStatus()+'</div>'+tierBadge(r,'marketing')+'</div>'
    + '<div class="kv">offer <b>'+(m.offer_incentive?'yes':'no')+'</b><span class="sep">·</span>'+(m.offer_type||'—')+' <b>'+Math.round(m.discount_pct||0)+'%</b><span class="sep">·</span>'+(m.channel||'—')+'<br/>LTV tier <b>'+(m.ltv_tier||'—')+'</b></div>'
    + note(m.rationale || m.message) + '</div>';

  const laneOps = '<div class="lane enter"><div class="lhead"><div class="lic ops">'+ic('truck',17)+'</div>'
    + '<div><div class="lname">Ops</div>'+laneStatus()+'</div>'+tierBadge(r,'ops')+'</div>'
    + '<div class="kv">target <b>'+(o.target_store_id||'—')+'</b><span class="sep">·</span>ETA <b>'+(o.eta_days||0)+'d</b><span class="sep">·</span>save <b>$'+num2(o.estimated_savings_usd)+'</b><br/>label <b>'+(o.label_id||'—')+'</b><span class="sep">·</span>'+(o.carrier||'—')+'</div>'
    + note(o.rationale) + '</div>';

  // meta
  const synth = parseTier(r.models_used && r.models_used['synthesis']);
  const artNames = (r.artifacts && r.artifacts.tool_artifacts) ? r.artifacts.tool_artifacts.map(a=>a.tool_name) : [];
  let meta = '<div class="meta">';
  meta += '<span class="m">'+ic('sparkles',14)+'synthesis <b>'+(synth.tier||'—')+(synth.resolved?' → '+synth.resolved:'')+'</b></span>';
  meta += '<span class="m">'+ic('clock',14)+'<b>'+(r.latency_ms||0)+' ms</b></span>';
  meta += '<span class="m">'+ic('db',14)+'memory <b>'+(r.memory_used?'on':'off')+'</b></span>';
  if(artNames.length) meta += '<span class="m">'+ic('pkg',14)+'MCP <b>'+artNames.join(', ')+'</b></span>';
  if(r.trace_url) meta += '<a class="m" href="'+r.trace_url+'" target="_blank" rel="noopener">'+ic('link',14)+'Langfuse trace</a>';
  meta += '</div>';

  const reason = '<details class="reason"><summary>'+ic('cpu',15)+'Reasoning &amp; raw decision'+ic('chev',16,'chev')+'</summary>'
    + '<div class="rbody"><div class="txt">'+esc(r.reasoning)+'</div><pre>'+esc(JSON.stringify(r,null,2))+'</pre></div></details>';

  return railHTML('done')
    + '<div class="decision '+kind+'">'
    +   '<div class="dtop">'
    +     '<span class="badge '+badgeCls+'">'+ic({reroute:'truck',exchange:'tag',flag:'shieldAlert'}[kind],14)+r.route+'</span>'
    +     '<span class="status-tag">'+ic(r.status==='COMPLETED'?'check':r.status==='REJECTED'?'x':'clock',13)+r.status+'</span>'
    +     '<span class="hero '+kind+'"><span class="v" id="heroVal">$0.00</span><div class="l">'+heroLabel+'</div></span>'
    +   '</div>'
    +   '<div class="headline">'+md(r.headline)+'</div>'
    +   '<div class="msg"><div class="who">'+ic('msg',13)+'Customer message</div>'+md(r.customer_message)+'</div>'
    +   approval
    + '</div>'
    + '<div class="lanes-h"><span class="t">Specialist agents</span><span class="n">4 lanes · per-tier model abstraction</span></div>'
    + '<div class="lanes">'+laneFraud+laneDemand+laneMktg+laneOps+'</div>'
    + meta + reason;
}

function revealResult(r){
  $('canvas').innerHTML = resultHTML(r);
  // count-up hero
  countUp($('heroVal'), Number(r.asset_recovery_usd)||0);
  // fill meters
  requestAnimationFrame(() => {
    document.querySelectorAll('.meter > i').forEach(el => { el.style.width = (el.dataset.w||0)+'%'; });
    // staggered lane entrance
    const lanes = document.querySelectorAll('.lane.enter');
    lanes.forEach((el,i) => {
      if(RM){ el.classList.remove('enter'); }
      else { setTimeout(() => el.classList.remove('enter'), 70 + i*95); }
    });
  });
}

function countUp(el, to){
  if(!el) return;
  if(RM){ el.textContent = fmtUSD(to); return; }
  const dur = 900, t0 = performance.now();
  function step(t){
    const p = Math.min((t - t0)/dur, 1);
    const e = 1 - Math.pow(1 - p, 3);
    el.textContent = fmtUSD(to * e);
    if(p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

/* ---------- submit / resolve ---------- */
async function submitReturn(){
  const go = $('go');
  go.disabled = true; $('gotx').textContent = 'Running 4 agents…'; $('goic').innerHTML = ic('loop',16,'spin');
  renderRunning();
  const body = {
    customer_id: $('customer').value,
    order_id: $('order').value,
    sku: $('sku').value,
    reason_text: $('reason').value,
    customer_zip: $('zip').value,
    photo_base64: photoB64,
  };
  try{
    const res = await fetch('/return', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const r = await res.json();
    if(r.error){ renderError(r.error); }
    else { revealResult(r); }
  }catch(e){ renderError(e.message); }
  go.disabled = false; $('gotx').textContent = 'Process return'; $('goic').innerHTML = ic('zap',16);
}
async function resolve(id, approved){
  document.querySelectorAll('.approval .btn').forEach(b => b.disabled = true);
  try{
    const res = await fetch('/returns/'+id+'/'+(approved?'approve':'reject'), {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const r = await res.json();
    if(r.error){ renderError(r.error); } else { revealResult(r); }
  }catch(e){ renderError(e.message); }
}
function renderError(msg){
  $('canvas').innerHTML = '<div class="errbox"><div class="eic">'+ic('alert',26)+'</div>'
    + '<div style="font-size:15px;font-weight:600;color:var(--ink);margin-bottom:6px">Something went wrong</div>'
    + '<div style="max-width:46ch;font-size:13px;line-height:1.6">'+esc(msg)+'</div></div>';
}

/* ---------- formatting ---------- */
function num(v){ return (v==null||isNaN(v)) ? '—' : Number(v).toFixed(2); }
function num2(v){ return (v==null||isNaN(v)) ? '0.00' : Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}); }
function fmtUSD(n){ return '$'+(Number(n)||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}); }
function fmtKm(v){ return (v==null||isNaN(v)) ? '—' : Number(v)+' km'; }
function note(s){ s = (s==null?'':String(s)).trim(); return s ? '<span class="note">'+esc(s)+'</span>' : ''; }
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
/* markdown-lite: escape first, then render bold/italic/line breaks (LLM copy often uses **bold**) */
function md(s){
  let t = esc(s);
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
  t = t.replace(/\n/g, '<br/>');
  return t;
}

init();
</script>
</body>
</html>
"""
