"""Mock retail data + query helpers (pure stdlib, no app/orchestrator imports).

Single source of truth shared by:
  * ``mcp_server.py``  — exposes get_regional_demand / generate_reroute_label / etc.
  * ``memory_seed.py`` — seeds customer history into long-term (USER) memory.

The numbers are fabricated but internally consistent so the demo tells a clear
story: rerouting a returned item to a nearby high-deficit store recovers far
more value than shipping it back to a distant central warehouse.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

PRODUCTS: dict[str, dict] = {
    "p1": {"name": "TrailRunner Hiking Boots", "category": "footwear", "price": 149.99},
    "p2": {"name": "AeroDown Winter Jacket", "category": "outerwear", "price": 229.99},
    "p3": {"name": "Pulse Wireless Earbuds", "category": "electronics", "price": 119.99},
    "p4": {"name": "Summit 40L Backpack", "category": "bags", "price": 89.99},
    "p5": {"name": "FlexFit Yoga Mat", "category": "fitness", "price": 39.99},
}

# Approximate distance (km) from any store to the central returns warehouse.
# Deliberately large so the reroute savings are dramatic.
CENTRAL_WAREHOUSE = {"id": "WH-CENTRAL", "city": "Reno, NV", "zip": "89501"}
CENTRAL_WAREHOUSE_AVG_KM = 3400.0

# Per-km reverse-logistics cost assumption (USD/km) used for savings math.
SHIP_COST_PER_KM = 0.012

# ---------------------------------------------------------------------------
# Geo — minimal ZIP -> (lat, lon) table + haversine
# ---------------------------------------------------------------------------

ZIP_COORDS: dict[str, tuple[float, float]] = {
    "10001": (40.7506, -73.9972),  # New York, NY
    "07302": (40.7178, -74.0431),  # Jersey City, NJ
    "02108": (42.3576, -71.0639),  # Boston, MA
    "19103": (39.9526, -75.1652),  # Philadelphia, PA
    "20001": (38.9101, -77.0147),  # Washington, DC
    "06103": (41.7658, -72.6734),  # Hartford, CT
    "21201": (39.2976, -76.6190),  # Baltimore, MD
    "94105": (37.7898, -122.3942),  # San Francisco, CA
    "89501": (39.5296, -119.8138),  # Reno, NV (warehouse)
}
_DEFAULT_COORD = ZIP_COORDS["10001"]


def _coord(zip_code: str) -> tuple[float, float]:
    return ZIP_COORDS.get((zip_code or "").strip(), _DEFAULT_COORD)


def haversine_km(zip_a: str, zip_b: str) -> float:
    """Great-circle distance between two ZIPs in kilometres."""
    lat1, lon1 = _coord(zip_a)
    lat2, lon2 = _coord(zip_b)
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return round(r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


# ---------------------------------------------------------------------------
# Stores: location + per-SKU stock and weekly trend velocity (units/week)
# ---------------------------------------------------------------------------
# inventory[sku] = {"stock": int, "velocity": float}

STORES: dict[str, dict] = {
    "NYC-05": {
        "city": "New York, NY",
        "zip": "10001",
        "inventory": {
            "p1": {"stock": 1, "velocity": 14.0},
            "p2": {"stock": 0, "velocity": 9.0},
            "p3": {"stock": 6, "velocity": 11.0},
            "p4": {"stock": 4, "velocity": 5.0},
            "p5": {"stock": 12, "velocity": 7.0},
        },
    },
    "JC-02": {
        "city": "Jersey City, NJ",
        "zip": "07302",
        "inventory": {
            "p1": {"stock": 0, "velocity": 18.0},  # boots: hot + empty -> high deficit
            "p2": {"stock": 3, "velocity": 6.0},
            "p3": {"stock": 2, "velocity": 13.0},
            "p4": {"stock": 1, "velocity": 8.0},
            "p5": {"stock": 9, "velocity": 4.0},
        },
    },
    "BOS-11": {
        "city": "Boston, MA",
        "zip": "02108",
        "inventory": {
            "p1": {"stock": 5, "velocity": 6.0},
            "p2": {"stock": 1, "velocity": 12.0},
            "p3": {"stock": 8, "velocity": 5.0},
            "p4": {"stock": 0, "velocity": 7.0},
            "p5": {"stock": 3, "velocity": 9.0},
        },
    },
    "PHL-07": {
        "city": "Philadelphia, PA",
        "zip": "19103",
        "inventory": {
            "p1": {"stock": 2, "velocity": 10.0},
            "p2": {"stock": 7, "velocity": 4.0},
            "p3": {"stock": 0, "velocity": 16.0},
            "p4": {"stock": 5, "velocity": 3.0},
            "p5": {"stock": 6, "velocity": 6.0},
        },
    },
    "DC-03": {
        "city": "Washington, DC",
        "zip": "20001",
        "inventory": {
            "p1": {"stock": 9, "velocity": 3.0},
            "p2": {"stock": 2, "velocity": 8.0},
            "p3": {"stock": 4, "velocity": 9.0},
            "p4": {"stock": 2, "velocity": 11.0},
            "p5": {"stock": 0, "velocity": 13.0},
        },
    },
    "SF-21": {
        "city": "San Francisco, CA",
        "zip": "94105",
        "inventory": {
            "p1": {"stock": 0, "velocity": 20.0},  # also hot, but far from NYC customers
            "p2": {"stock": 0, "velocity": 15.0},
            "p3": {"stock": 1, "velocity": 18.0},
            "p4": {"stock": 0, "velocity": 12.0},
            "p5": {"stock": 2, "velocity": 10.0},
        },
    },
}


def _deficit_score(stock: int, velocity: float) -> float:
    """Unmet demand pressure: high velocity + low stock -> high deficit (0..~1)."""
    weeks_of_cover = (stock + 0.5) / max(velocity, 0.1)
    # Map weeks-of-cover to 0..1 (less cover => closer to 1).
    score = 1.0 / (1.0 + weeks_of_cover)
    return round(score, 3)


# ---------------------------------------------------------------------------
# Customers: CRM-style history (also seeded into long-term memory)
# ---------------------------------------------------------------------------

CUSTOMERS: dict[str, dict] = {
    "cust_alpha": {
        "name": "Alicia Romero",
        "ltv_tier": "platinum",
        "lifetime_value": 4820.0,
        "return_count": 2,
        "fraud_flags": 0,
        "preferred_channel": "email",
        "past_exchanges": 2,
        "notes": "Loyal high-value customer; returns are rare and always genuine.",
    },
    "cust_beta": {
        "name": "Ben Tanaka",
        "ltv_tier": "gold",
        "lifetime_value": 1560.0,
        "return_count": 3,
        "fraud_flags": 0,
        "preferred_channel": "sms",
        "past_exchanges": 1,
        "notes": "Solid repeat buyer; price-sensitive, responds well to exchange offers.",
    },
    "cust_frank": {
        "name": "Frank Delgado",
        "ltv_tier": "bronze",
        "lifetime_value": 210.0,
        "return_count": 11,
        "fraud_flags": 2,
        "preferred_channel": "email",
        "past_exchanges": 0,
        "notes": (
            "Serial returner. Two prior fraud flags for wardrobing (worn items returned "
            "as 'defective'). High scrutiny — photos frequently inconsistent with claims."
        ),
    },
    "cust_demo": {
        "name": "Dana Okafor",
        "ltv_tier": "silver",
        "lifetime_value": 740.0,
        "return_count": 4,
        "fraud_flags": 0,
        "preferred_channel": "app_push",
        "past_exchanges": 1,
        "notes": "Average customer; no fraud history.",
    },
}

_DEFAULT_CUSTOMER = {
    "name": "New Customer",
    "ltv_tier": "unknown",
    "lifetime_value": 0.0,
    "return_count": 0,
    "fraud_flags": 0,
    "preferred_channel": "email",
    "past_exchanges": 0,
    "notes": "No prior history on file.",
}


# ---------------------------------------------------------------------------
# Query helpers (called by the MCP tools)
# ---------------------------------------------------------------------------


def product_info(sku: str) -> dict:
    p = PRODUCTS.get(sku)
    if not p:
        return {"sku": sku, "name": f"Unknown product {sku}", "category": "unknown", "price": 0.0}
    return {"sku": sku, **p}


def regional_demand(sku: str, customer_zip: str, radius_km: float) -> dict:
    """Stores within ``radius_km`` of the customer, ranked by deficit for ``sku``."""
    rows: list[dict] = []
    for store_id, store in STORES.items():
        inv = store["inventory"].get(sku)
        if not inv:
            continue
        dist = haversine_km(customer_zip, store["zip"])
        if dist > radius_km:
            continue
        rows.append(
            {
                "store_id": store_id,
                "city": store["city"],
                "distance_km": dist,
                "current_stock": inv["stock"],
                "trend_velocity": inv["velocity"],
                "deficit_score": _deficit_score(inv["stock"], inv["velocity"]),
            }
        )
    # Rank: highest deficit first, then closest.
    rows.sort(key=lambda r: (-r["deficit_score"], r["distance_km"]))
    best = rows[0] if rows else None
    return {
        "sku": sku,
        "product": product_info(sku),
        "customer_zip": customer_zip,
        "radius_km": radius_km,
        "store_count": len(rows),
        "best_store_id": best["store_id"] if best else "",
        "best_store_deficit": best["deficit_score"] if best else 0.0,
        "stores": rows,
    }


def reroute_label(sku: str, store_id: str, customer_zip: str) -> dict:
    """Generate a prepaid reroute label to ``store_id`` + compute savings."""
    store = STORES.get(store_id)
    if not store:
        return {"error": f"Unknown store {store_id!r}"}
    dist_to_store = haversine_km(customer_zip, store["zip"])
    saved_km = max(CENTRAL_WAREHOUSE_AVG_KM - dist_to_store, 0.0)
    savings = round(saved_km * SHIP_COST_PER_KM + 6.50, 2)  # + handling differential
    label_id = f"RRT-{store_id}-{sku}-{abs(hash((sku, store_id, customer_zip))) % 100000:05d}"
    eta = 1 if dist_to_store < 60 else 2 if dist_to_store < 200 else 3
    return {
        "label_id": label_id,
        "label_url": f"https://labels.returns.example/{label_id}.pdf",
        "carrier": "RegionalExpress" if dist_to_store < 200 else "FedEx",
        "from_zip": customer_zip,
        "to_store_id": store_id,
        "to_city": store["city"],
        "distance_km": dist_to_store,
        "eta_days": eta,
        "estimated_savings_usd": savings,
        "vs_central_km": CENTRAL_WAREHOUSE_AVG_KM,
    }


def apply_inventory(store_id: str, sku: str, delta: int) -> dict:
    store = STORES.get(store_id)
    if not store:
        return {"error": f"Unknown store {store_id!r}"}
    inv = store["inventory"].setdefault(sku, {"stock": 0, "velocity": 0.0})
    inv["stock"] = max(inv["stock"] + delta, 0)
    return {
        "store_id": store_id,
        "sku": sku,
        "delta": delta,
        "new_stock": inv["stock"],
        "deficit_score": _deficit_score(inv["stock"], inv["velocity"]),
    }


def customer_history(customer_id: str) -> dict:
    c = CUSTOMERS.get(customer_id, _DEFAULT_CUSTOMER)
    return {"customer_id": customer_id, **c}
