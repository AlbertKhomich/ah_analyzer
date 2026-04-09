import csv
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

# =========================
# CONFIG
# =========================

SNAPSHOT_CSV = "ah_snapshot.csv"
RANKING_JSON = "ranking.json"
OUTPUT_JSON = "craft_plan.json"
OUTPUT_CSV = "craft_plan.csv"

# AH cut on successful sale
AH_CUT = 0.05

# Fallback prices in copper for items that may not appear in AH snapshot.
# Adjust if needed.
FALLBACK_PRICES = {
    "Crystal Vial": 500,          # 5s
    "Light Parchment": 1500,      # 15s
    "Resilient Parchment": 1500,  # normalized vendor fallback
    "Heavy Parchment": 1500,      # normalized vendor fallback
}

# Normalize some recipe reagent names if needed
NAME_ALIASES = {
    "Resilient Parchment": "Light Parchment",
    "Heavy Parchment": "Light Parchment",
}

# Base stock caps by category and tier
BASE_STOCK = {
    "glyph": {"S": 2, "A": 1, "B": 1, "C": 0},
    "potion": {"S": 40, "A": 25, "B": 12, "C": 0},
    "flask": {"S": 20, "A": 12, "B": 8, "C": 0},
    "shoulder_inscription": {"S": 5, "A": 4, "B": 2, "C": 0},
    "tailoring_spellthread": {"S": 5, "A": 3, "B": 1, "C": 0},
    "tailoring_bag": {"S": 2, "A": 1, "B": 1, "C": 0},
    "tailoring_epic": {"S": 1, "A": 1, "B": 1, "C": 0},
    "tailoring_pvp": {"S": 1, "A": 1, "B": 1, "C": 0},
}

# =========================
# HELPERS
# =========================

def normalize_name(name: str) -> str:
    name = name.strip()
    # strip item level suffixes like "(476)" or "(35)"
    name = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()
    return NAME_ALIASES.get(name, name)

def copper_to_gold(copper: int) -> str:
    g = copper // 10000
    s = (copper % 10000) // 100
    c = copper % 100
    return f"{g}g {s}s {c}c"

def load_snapshot(csv_path: str) -> Dict[str, Dict[str, Any]]:
    items: Dict[str, Dict[str, Any]] = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_name = row["Name"]
            name = normalize_name(raw_name)
            price = int(row["Price"])
            available = int(row["Available"])
            items[name] = {
                "raw_name": raw_name,
                "price": price,
                "available": available,
                "item_level": row.get("Item Level", ""),
            }
    return items

def load_ranking(json_path: str) -> Dict[str, Any]:
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

def reagent_unit_price(name: str, snapshot: Dict[str, Dict[str, Any]]) -> Optional[int]:
    normalized = normalize_name(name)
    if normalized in snapshot:
        return snapshot[normalized]["price"]
    if normalized in FALLBACK_PRICES:
        return FALLBACK_PRICES[normalized]
    return None

def compute_material_cost(reagents: Optional[List[Dict[str, Any]]], snapshot: Dict[str, Dict[str, Any]]) -> Optional[int]:
    if not reagents:
        return None
    total = 0
    for reagent in reagents:
        r_name = reagent["item"]
        qty = int(reagent["qty"])
        unit_price = reagent_unit_price(r_name, snapshot)
        if unit_price is None:
            return None
        total += unit_price * qty
    return total

def recommended_quantity(
    category: str,
    tier: str,
    rank: int,
    profit: Optional[int],
    roi: Optional[float],
    available: int
) -> int:
    base = BASE_STOCK.get(category, {"S": 1, "A": 1, "B": 0, "C": 0}).get(tier, 0)

    if base == 0 or profit is None or profit <= 0:
        return 0

    # Profit multiplier
    multiplier = 1.0
    if roi is not None:
        if roi >= 1.00:
            multiplier *= 1.8
        elif roi >= 0.50:
            multiplier *= 1.4
        elif roi >= 0.20:
            multiplier *= 1.1
        elif roi < 0.05:
            multiplier *= 0.5

    # Availability pressure
    # lots of competition => cut stock
    if available >= 3000:
        multiplier *= 0.35
    elif available >= 1500:
        multiplier *= 0.50
    elif available >= 500:
        multiplier *= 0.70
    elif available >= 100:
        multiplier *= 0.90
    elif available <= 30:
        multiplier *= 1.3

    # ranking bonus
    if rank <= 10:
        multiplier *= 1.2
    elif rank <= 20:
        multiplier *= 1.1

    qty = max(0, math.floor(base * multiplier))

    # Keep glyphs sane
    if category == "glyph":
        qty = min(qty, 3)

    # Bags / epics should stay conservative
    if category in {"tailoring_bag", "tailoring_epic", "tailoring_pvp"}:
        qty = min(qty, 2)

    return qty

def build_plan(snapshot: Dict[str, Dict[str, Any]], ranking: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = []

    for entry in ranking["priority_queue"]:
        item_name = normalize_name(entry["item"])
        category = entry["category"]
        tier = entry["tier"]
        rank = entry["rank"]
        reagents = entry.get("reagents")

        if item_name not in snapshot:
            results.append({
                "rank": rank,
                "item": item_name,
                "category": category,
                "tier": tier,
                "status": "missing_output_price",
                "material_cost": None,
                "sell_price": None,
                "net_sell_after_cut": None,
                "profit": None,
                "roi": None,
                "available": None,
                "recommended_quantity": 0,
                "reason": "Output item not found in AH snapshot"
            })
            continue

        sell_price = snapshot[item_name]["price"]
        available = snapshot[item_name]["available"]
        mat_cost = compute_material_cost(reagents, snapshot)
        net_sell = math.floor(sell_price * (1 - AH_CUT))

        if mat_cost is None:
            results.append({
                "rank": rank,
                "item": item_name,
                "category": category,
                "tier": tier,
                "status": "missing_reagent_price",
                "material_cost": None,
                "sell_price": sell_price,
                "net_sell_after_cut": net_sell,
                "profit": None,
                "roi": None,
                "available": available,
                "recommended_quantity": 0,
                "reason": "At least one reagent price missing"
            })
            continue

        profit = net_sell - mat_cost
        roi = profit / mat_cost if mat_cost > 0 else None
        qty = recommended_quantity(category, tier, rank, profit, roi, available)

        results.append({
            "rank": rank,
            "item": item_name,
            "category": category,
            "tier": tier,
            "status": "ok" if profit > 0 else "skip",
            "material_cost": mat_cost,
            "sell_price": sell_price,
            "net_sell_after_cut": net_sell,
            "profit": profit,
            "roi": round(roi, 4) if roi is not None else None,
            "available": available,
            "recommended_quantity": qty,
            "reason": entry.get("reason", "")
        })

    # Sort by:
    # 1. profitable first
    # 2. higher profit first
    # 3. better rank first
    results.sort(
        key=lambda x: (
            0 if x["status"] == "ok" else 1,
            -(x["profit"] if x["profit"] is not None else -10**12),
            x["rank"]
        )
    )
    return results

def write_outputs(results: List[Dict[str, Any]], output_json: str, output_csv: str) -> None:
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    with open(output_csv, "w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "rank", "item", "category", "tier", "status",
            "material_cost", "sell_price", "net_sell_after_cut",
            "profit", "roi", "available", "recommended_quantity", "reason"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

def print_top(results: List[Dict[str, Any]], limit: int = 20) -> None:
    print("\n=== TOP CRAFTS ===")
    shown = 0
    for row in results:
        if row["status"] != "ok":
            continue
        shown += 1
        print(
            f"{row['rank']:>2}. {row['item']}"
            f" | profit={copper_to_gold(row['profit'])}"
            f" | roi={row['roi']:.2%}"
            f" | ah={copper_to_gold(row['sell_price'])}"
            f" | mats={copper_to_gold(row['material_cost'])}"
            f" | avail={row['available']}"
            f" | craft={row['recommended_quantity']}"
        )
        if shown >= limit:
            break

    print("\n=== SKIP / INCOMPLETE ===")
    for row in results:
        if row["status"] == "ok":
            continue
        print(
            f"{row['rank']:>2}. {row['item']} | status={row['status']} | reason={row['reason']}"
        )

def main() -> None:
    snapshot = load_snapshot(SNAPSHOT_CSV)
    ranking = load_ranking(RANKING_JSON)
    results = build_plan(snapshot, ranking)
    write_outputs(results, OUTPUT_JSON, OUTPUT_CSV)
    print_top(results, limit=20)
    print(f"\nSaved: {OUTPUT_JSON}")
    print(f"Saved: {OUTPUT_CSV}")

if __name__ == "__main__":
    main()