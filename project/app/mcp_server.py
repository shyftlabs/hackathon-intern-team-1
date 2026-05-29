"""Inventory & Logistics MCP server (stdio transport).

Exposes the operational tools the Demand and Ops agents call. Continuum spawns
this file as a subprocess via ``MCPServerStdio`` (see ``engine.py``):

    MCPServerStdio({"command": sys.executable, "args": ["mcp_server.py"], "cwd": <app dir>})

Run standalone for a quick smoke test:
    python mcp_server.py            # serves on stdio (will just block waiting)
    python mcp_server.py --selftest # prints sample tool outputs and exits
"""

from __future__ import annotations

import asyncio
import os
import sys

# Make ``seed_data`` importable whether launched as a subprocess (cwd=app dir)
# or directly. We add this file's own directory to sys.path and import flat.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seed_data  # noqa: E402  (flat import: same directory)

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("inventory-logistics")


@mcp.tool()
def get_regional_demand(sku: str, customer_zip: str = "10001", radius_km: int = 150) -> dict:
    """Return stores within radius_km of the customer ranked by unmet demand
    (deficit_score) for this SKU, including current stock, weekly trend
    velocity, and distance. The first store is the best reroute target."""
    return seed_data.regional_demand(sku, customer_zip, float(radius_km))


@mcp.tool()
def get_customer_history(customer_id: str) -> dict:
    """CRM lookup: lifetime value, LTV tier, total returns, prior fraud flags,
    preferred contact channel, and past exchange count for a customer."""
    return seed_data.customer_history(customer_id)


@mcp.tool()
def generate_reroute_label(sku: str, store_id: str, customer_zip: str = "10001") -> dict:
    """Generate a prepaid shipping label rerouting a returned SKU to a nearby
    store instead of the central warehouse. Returns label id/url, carrier, ETA,
    and the estimated reverse-logistics savings versus the central warehouse."""
    return seed_data.reroute_label(sku, store_id, customer_zip)


@mcp.tool()
def update_inventory(store_id: str, sku: str, delta: int = 1) -> dict:
    """Adjust a store's on-hand stock for a SKU by ``delta`` (use +1 when a
    rerouted return arrives). Returns the new stock level and deficit score."""
    return seed_data.apply_inventory(store_id, sku, int(delta))


def _selftest() -> None:
    import json

    print("get_regional_demand('p1', '10001', 150):")
    print(json.dumps(seed_data.regional_demand("p1", "10001", 150.0), indent=2))
    print("\ngenerate_reroute_label('p1', 'JC-02', '10001'):")
    print(json.dumps(seed_data.reroute_label("p1", "JC-02", "10001"), indent=2))
    print("\nupdate_inventory('JC-02', 'p1', 1):")
    print(json.dumps(seed_data.apply_inventory("JC-02", "p1", 1), indent=2))
    print("\nget_customer_history('cust_frank'):")
    print(json.dumps(seed_data.customer_history("cust_frank"), indent=2))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        asyncio.run(mcp.run_stdio_async())
