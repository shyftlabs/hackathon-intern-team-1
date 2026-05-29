#!/usr/bin/env python3
"""CLI end-to-end demo for the Returns Optimization Engine (no web server).

Runs three representative returns through the full multi-agent pipeline and
prints the decision for each:

  1. cust_alpha (platinum, clean)  -> exchange-offer
  2. cust_demo  (silver, clean)    -> reroute-label
  3. cust_frank (serial returner)  -> flag-for-review, then approve it

Usage:
    ../continuum/.venv/bin/python run_demo.py
    ../continuum/.venv/bin/python run_demo.py --scenario frank
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import app  # noqa: F401  -- triggers .env bootstrap before orchestrator import
from app.engine import ReturnsEngine
from app.sample_image import SAMPLE_CLEAN_B64, SAMPLE_DAMAGED_B64
from app.schemas import ReturnRequest

SCENARIOS = {
    "alpha": ReturnRequest(
        customer_id="cust_alpha", order_id="ORD-5001", sku="p2",
        reason_text="Jacket is a bit too warm for my climate, would prefer a lighter one.",
        photo_base64=SAMPLE_CLEAN_B64, customer_zip="10001",
    ),
    "demo": ReturnRequest(
        customer_id="cust_walkin", order_id="ORD-5002", sku="p1",
        reason_text="Boots are fine but I changed my mind — I just want a refund, no exchange.",
        photo_base64=SAMPLE_CLEAN_B64, customer_zip="07302",
    ),
    "frank": ReturnRequest(
        customer_id="cust_frank", order_id="ORD-5003", sku="p3",
        reason_text="Earbuds arrived defective and won't charge.",
        photo_base64=SAMPLE_DAMAGED_B64, customer_zip="07302",
    ),
}


def _print(result) -> None:
    d = result.model_dump()
    fraud = d.pop("fraud", None)
    demand = d.pop("demand", None)
    incentive = d.pop("incentive", None)
    ops = d.pop("ops", None)
    print("=" * 78)
    print(f"  RETURN {result.return_id}  ->  {result.route.upper()}  [{result.status}]")
    print("=" * 78)
    print(f"  Headline        : {result.headline}")
    print(f"  Asset recovery  : ${result.asset_recovery_usd:.2f}")
    print(f"  Latency         : {result.latency_ms} ms   memory={result.memory_used}")
    print(f"  Reasoning       : {result.reasoning}")
    print(f"  Customer message: {result.customer_message}")
    print("  --- lane outputs ---")
    if fraud:
        print(f"  fraud     : risk={fraud['risk_score']} flag={fraud['flag_for_review']} "
              f"damage={fraud['damage_level']} :: {fraud['flag_reason']}")
    if demand:
        print(f"  demand    : best={demand['best_store_id']} deficit={demand['best_store_deficit']} "
              f"@ {demand['distance_km']}km :: {demand['summary']}")
    if incentive:
        print(f"  marketing : offer={incentive['offer_incentive']} {incentive['offer_type']} "
              f"{incentive['discount_pct']}% via {incentive['channel']} (tier={incentive['ltv_tier']})")
    if ops:
        print(f"  ops       : reroute={ops['reroute']} -> {ops['target_store_id']} "
              f"label={ops['label_id']} eta={ops['eta_days']}d save=${ops['estimated_savings_usd']}")
    print("  --- model abstraction (gateway tiers) ---")
    for lane, desc in result.models_used.items():
        print(f"  {lane:9s}: {desc}")
    if result.trace_url:
        print(f"  Langfuse trace  : {result.trace_url}")
    print()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=[*SCENARIOS, "all"], default="all")
    args = ap.parse_args()

    engine = ReturnsEngine()
    print("Initializing ReturnsEngine (lifecycle + MCP + memory)...")
    await engine.initialize()
    try:
        keys = [args.scenario] if args.scenario != "all" else list(SCENARIOS)
        for key in keys:
            print(f"\n>>> Scenario '{key}'")
            result = await engine.process_return(SCENARIOS[key])
            _print(result)
            if result.status == "PENDING_APPROVAL":
                print(f"  ... human-in-the-loop: approving {result.return_id} as 'ops-team' ...\n")
                approved = await engine.resolve_approval(result.return_id, approved=True)
                _print(approved)
    finally:
        await engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
