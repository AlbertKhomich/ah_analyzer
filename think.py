import csv
import json
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# =========================
# CONFIG
# =========================

SNAPSHOT_CSV = "ah_snapshot.csv"
CLASS_SPEC_JSON = "class_spec_items.json"
CRAFTING_JSON = "crafting_data.json"
OUTPUT_JSON = "craft_plan.json"
OUTPUT_CSV = "craft_plan.csv"
PRICING_DEBUG_JSON = "pricing_debug.json"

# AH cut on successful sale
AH_CUT = 0.05

# Fallback prices in copper for items that may not appear in AH snapshot.
# Adjust if needed.
FALLBACK_PRICES = {
    "Crystal Vial": 500,
    "Light Parchment": 1500,
    "Resilient Parchment": 1500,
    "Heavy Parchment": 1500,
}

NON_AH_REAGENT_PRICES = {
    "Spirit of Harmony": 0,
}

FORCE_CRAFTED_COST_ITEMS = {
    "Imperial Silk",
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
    "alchemy_transmutation": {"S": 2, "A": 1, "B": 1, "C": 0},
    "tailoring_spellthread": {"S": 5, "A": 3, "B": 1, "C": 0},
    "tailoring_bag": {"S": 2, "A": 1, "B": 1, "C": 0},
    "tailoring_epic": {"S": 1, "A": 1, "B": 1, "C": 0},
    "tailoring_pvp": {"S": 1, "A": 1, "B": 1, "C": 0},
}

TIER_PRIORITY = {"S": 4, "A": 3, "B": 2, "C": 1}

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

def load_json(json_path: str) -> Dict[str, Any]:
    with open(json_path, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def get_recipe_entries(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "craft_targets" in data:
        return data["craft_targets"]
    if "priority_queue" in data:
        return data["priority_queue"]
    raise ValueError("Expected 'craft_targets' or 'priority_queue' in craft data JSON.")

def get_named_entry(entries: Dict[str, Any], name: str) -> Optional[Tuple[str, Any]]:
    normalized = normalize_name(name)
    for entry_name, entry_data in entries.items():
        if normalize_name(entry_name) == normalized:
            return entry_name, entry_data
    return None

def get_supporting_recipes(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("supporting_recipes", {})

def build_recipe_lookup(crafting_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for entry in get_recipe_entries(crafting_data):
        lookup[normalize_name(entry["item"])] = entry
    return lookup

def build_class_spec_usage_lookup(class_spec_data: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    usage: Dict[str, Dict[str, Set[str]]] = {}

    for class_name, class_block in class_spec_data.get("classes", {}).items():
        for spec_name, spec_block in class_block.items():
            spec_id = f"{class_name}:{spec_name}"

            for item_name in spec_block.get("likely_items", []):
                normalized = normalize_name(item_name)
                item_usage = usage.setdefault(
                    normalized,
                    {"likely_specs": set(), "situational_specs": set()},
                )
                item_usage["likely_specs"].add(spec_id)

            for item_name in spec_block.get("situational_items", []):
                normalized = normalize_name(item_name)
                item_usage = usage.setdefault(
                    normalized,
                    {"likely_specs": set(), "situational_specs": set()},
                )
                item_usage["situational_specs"].add(spec_id)

    return {
        item_name: {
            "likely_spec_count": len(item_usage["likely_specs"]),
            "situational_spec_count": len(item_usage["situational_specs"] - item_usage["likely_specs"]),
        }
        for item_name, item_usage in usage.items()
    }

def build_planner_entries(class_spec_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    item_index = class_spec_data.get("item_index", {})
    usage_lookup = build_class_spec_usage_lookup(class_spec_data)
    max_rank = max((int(data.get("rank", 0)) for data in item_index.values()), default=0)
    entries: List[Dict[str, Any]] = []

    for item_name, item_data in item_index.items():
        normalized = normalize_name(item_name)
        rank = int(item_data["rank"])
        tier = item_data.get("tier", "C")
        usage = usage_lookup.get(
            normalized,
            {"likely_spec_count": 0, "situational_spec_count": 0},
        )

        class_spec_score = (
            TIER_PRIORITY.get(tier, 0) * 1000
            + (max_rank - rank + 1) * 10
            + usage["likely_spec_count"] * 25
            + usage["situational_spec_count"] * 10
        )

        entries.append({
            "item": normalized,
            "rank": rank,
            "category": item_data["category"],
            "tier": tier,
            "reason": item_data.get("reason", ""),
            "class_spec_score": class_spec_score,
            "likely_spec_count": usage["likely_spec_count"],
            "situational_spec_count": usage["situational_spec_count"],
        })

    return entries

def format_qty(qty: float) -> str:
    if abs(qty - round(qty)) < 1e-9:
        return str(int(round(qty)))
    return f"{qty:.2f}".rstrip("0").rstrip(".")

def reagent_unit_price(name: str, snapshot: Dict[str, Dict[str, Any]]) -> Optional[int]:
    normalized = normalize_name(name)
    if normalized in snapshot:
        return snapshot[normalized]["price"]
    if normalized in NON_AH_REAGENT_PRICES:
        return NON_AH_REAGENT_PRICES[normalized]
    if normalized in FALLBACK_PRICES:
        return FALLBACK_PRICES[normalized]
    return None

def resolve_reagent_list(
    reagents: List[Dict[str, Any]],
    snapshot: Dict[str, Dict[str, Any]],
    crafting_data: Dict[str, Any],
    recipe_lookup: Dict[str, Dict[str, Any]],
    cost_cache: Dict[str, Dict[str, Any]],
    stack: Set[str],
) -> Optional[Dict[str, Any]]:
    total_cost = 0.0
    chain_parts: List[str] = []
    components: List[Dict[str, Any]] = []

    for reagent in reagents:
        reagent_name = normalize_name(reagent["item"])
        qty = float(reagent["qty"])
        resolved = resolve_unit_cost(
            reagent_name,
            snapshot,
            crafting_data,
            recipe_lookup,
            cost_cache,
            stack,
        )
        if resolved is None:
            return None

        line_cost = resolved["unit_cost"] * qty
        total_cost += line_cost
        chain_parts.append(f"{format_qty(qty)}x {reagent_name}->{resolved['chain']}")
        components.append({
            "item": reagent_name,
            "qty": int(qty) if abs(qty - round(qty)) < 1e-9 else qty,
            "unit_cost": resolved["unit_cost"],
            "total_cost": int(round(line_cost)),
            "source_type": resolved["source_type"],
            "source_summary": resolved["source_summary"],
            "source_detail": resolved["source_detail"],
            "source_chain": resolved["chain"],
        })

    return {
        "total_cost": int(round(total_cost)),
        "chain": "; ".join(chain_parts),
        "components": components,
    }

def resolve_milling_cost(
    item_name: str,
    snapshot: Dict[str, Dict[str, Any]],
    crafting_data: Dict[str, Any],
    recipe_lookup: Dict[str, Dict[str, Any]],
    cost_cache: Dict[str, Dict[str, Any]],
    stack: Set[str],
) -> Optional[Dict[str, Any]]:
    milling = get_supporting_recipes(crafting_data).get("milling", {})
    pigment_entry = get_named_entry(milling.get("pigments", {}), item_name)
    if pigment_entry is None:
        return None

    pigment_name, pigment_data = pigment_entry
    rules = milling.get("rules", {})
    herbs_per_mill = float(rules.get("herbs_per_mill", 5))
    quality = pigment_data.get("quality", "common")

    expected_yield = pigment_data.get("expected_pigment_per_mill")
    if expected_yield is None:
        if quality == "common":
            expected_yield = rules.get("expected_common_pigment_per_mill")
        else:
            expected_yield = rules.get("expected_uncommon_pigment_per_mill")

    if not expected_yield or expected_yield <= 0:
        return None

    best_option = None
    for herb in pigment_data.get("milled_from", []):
        herb_name = normalize_name(herb)
        herb_cost = resolve_unit_cost(
            herb_name,
            snapshot,
            crafting_data,
            recipe_lookup,
            cost_cache,
            stack,
        )
        if herb_cost is None:
            continue

        unit_cost = int(round((herb_cost["unit_cost"] * herbs_per_mill) / float(expected_yield)))
        option = {
            "item": pigment_name,
            "unit_cost": unit_cost,
            "source_type": "milling",
            "source_summary": f"mill {herb_name}",
            "source_detail": (
                f"Milling via {format_qty(herbs_per_mill)}x {herb_name} per cast "
                f"with {expected_yield} expected {quality} pigment per mill."
            ),
            "chain": f"mill:{herb_name}",
        }
        if best_option is None or option["unit_cost"] < best_option["unit_cost"]:
            best_option = option

    return best_option

def resolve_unit_cost(
    item_name: str,
    snapshot: Dict[str, Dict[str, Any]],
    crafting_data: Dict[str, Any],
    recipe_lookup: Dict[str, Dict[str, Any]],
    cost_cache: Dict[str, Dict[str, Any]],
    stack: Set[str],
) -> Optional[Dict[str, Any]]:
    normalized = normalize_name(item_name)
    if normalized in cost_cache:
        return cost_cache[normalized]
    if normalized in stack:
        return None

    stack.add(normalized)
    options: List[Dict[str, Any]] = []
    support = get_supporting_recipes(crafting_data)
    inscription = support.get("inscription", {})
    tailoring = support.get("tailoring_subcrafts", {})

    if normalized in snapshot:
        options.append({
            "item": normalized,
            "unit_cost": snapshot[normalized]["price"],
            "source_type": "market",
            "source_summary": "AH",
            "source_detail": "Direct auction house market price.",
            "chain": "AH",
        })

    recipe_entry = recipe_lookup.get(normalized)
    if recipe_entry is not None and recipe_entry.get("reagents"):
        crafted = resolve_reagent_list(
            recipe_entry["reagents"],
            snapshot,
            crafting_data,
            recipe_lookup,
            cost_cache,
            stack,
        )
        if crafted is not None:
            options.append({
                "item": normalized,
                "unit_cost": crafted["total_cost"],
                "source_type": "crafted",
                "source_summary": "craft",
                "source_detail": f"Crafted via recipe entry from {crafted['chain']}.",
                "chain": f"craft({crafted['chain']})",
            })

    ink_entry = get_named_entry(inscription.get("inks", {}), normalized)
    if ink_entry is not None and ink_entry[1].get("crafted_from"):
        crafted = resolve_reagent_list(
            ink_entry[1]["crafted_from"],
            snapshot,
            crafting_data,
            recipe_lookup,
            cost_cache,
            stack,
        )
        if crafted is not None:
            options.append({
                "item": normalized,
                "unit_cost": crafted["total_cost"],
                "source_type": "crafted",
                "source_summary": "craft",
                "source_detail": f"Crafted from {crafted['chain']}.",
                "chain": f"craft({crafted['chain']})",
            })

    tailoring_entry = get_named_entry(tailoring, normalized)
    if tailoring_entry is not None and tailoring_entry[1].get("crafted_from"):
        crafted = resolve_reagent_list(
            tailoring_entry[1]["crafted_from"],
            snapshot,
            crafting_data,
            recipe_lookup,
            cost_cache,
            stack,
        )
        if crafted is not None:
            options.append({
                "item": normalized,
                "unit_cost": crafted["total_cost"],
                "source_type": "crafted",
                "source_summary": "craft",
                "source_detail": f"Crafted from {crafted['chain']}.",
                "chain": f"craft({crafted['chain']})",
            })

    trade_entry = get_named_entry(inscription.get("vendor_trades", {}), normalized)
    if trade_entry is not None and trade_entry[1].get("cost"):
        traded = resolve_reagent_list(
            trade_entry[1]["cost"],
            snapshot,
            crafting_data,
            recipe_lookup,
            cost_cache,
            stack,
        )
        if traded is not None:
            note = trade_entry[1].get("note", "Vendor trade path.")
            options.append({
                "item": normalized,
                "unit_cost": traded["total_cost"],
                "source_type": "vendor_trade",
                "source_summary": "trade",
                "source_detail": f"{note} Cost path: {traded['chain']}.",
                "chain": f"trade({traded['chain']})",
            })

    milling_option = resolve_milling_cost(
        normalized,
        snapshot,
        crafting_data,
        recipe_lookup,
        cost_cache,
        stack,
    )
    if milling_option is not None:
        options.append(milling_option)

    if normalized in NON_AH_REAGENT_PRICES:
        options.append({
            "item": normalized,
            "unit_cost": NON_AH_REAGENT_PRICES[normalized],
            "source_type": "non_ah",
            "source_summary": "non-AH",
            "source_detail": "Non-AH reagent excluded from gold-spend ranking.",
            "chain": "non-AH",
        })

    if normalized in FALLBACK_PRICES:
        options.append({
            "item": normalized,
            "unit_cost": FALLBACK_PRICES[normalized],
            "source_type": "fallback",
            "source_summary": "vendor",
            "source_detail": "Fixed vendor fallback price.",
            "chain": "vendor",
        })

    stack.remove(normalized)
    if not options:
        return None

    best_options = options
    if normalized in FORCE_CRAFTED_COST_ITEMS:
        crafted_options = [
            option for option in options
            if option["source_type"] in {"crafted", "vendor_trade", "milling"}
        ]
        if crafted_options:
            best_options = crafted_options

    best_option = min(best_options, key=lambda option: option["unit_cost"])
    cost_cache[normalized] = best_option
    return best_option

def compute_material_cost_details(
    reagents: Optional[List[Dict[str, Any]]],
    snapshot: Dict[str, Dict[str, Any]],
    crafting_data: Dict[str, Any],
    recipe_lookup: Dict[str, Dict[str, Any]],
    cost_cache: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[int], List[Dict[str, Any]], str]:
    if not reagents:
        return None, [], ""

    resolved = resolve_reagent_list(
        reagents,
        snapshot,
        crafting_data,
        recipe_lookup,
        cost_cache,
        set(),
    )
    if resolved is None:
        return None, [], ""
    return resolved["total_cost"], resolved["components"], resolved["chain"]

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

def build_plan(
    snapshot: Dict[str, Dict[str, Any]],
    planner_entries: List[Dict[str, Any]],
    crafting_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    results = []
    cost_cache: Dict[str, Dict[str, Any]] = {}
    recipe_lookup = build_recipe_lookup(crafting_data)

    for entry in planner_entries:
        item_name = entry["item"]
        category = entry["category"]
        tier = entry["tier"]
        rank = entry["rank"]
        recipe_entry = recipe_lookup.get(item_name)

        if recipe_entry is None:
            sell_price = snapshot[item_name]["price"] if item_name in snapshot else None
            available = snapshot[item_name]["available"] if item_name in snapshot else None
            net_sell = math.floor(sell_price * (1 - AH_CUT)) if sell_price is not None else None
            results.append({
                "rank": rank,
                "item": item_name,
                "category": category,
                "tier": tier,
                "status": "missing_recipe_data",
                "class_spec_score": entry["class_spec_score"],
                "likely_spec_count": entry["likely_spec_count"],
                "situational_spec_count": entry["situational_spec_count"],
                "material_cost": None,
                "sell_price": sell_price,
                "net_sell_after_cut": net_sell,
                "profit": None,
                "roi": None,
                "available": available,
                "recommended_quantity": 0,
                "material_sources": [],
                "material_source_summary": "",
                "reason": "Recipe entry not found in crafting data",
            })
            continue

        if item_name not in snapshot:
            results.append({
                "rank": rank,
                "item": item_name,
                "category": category,
                "tier": tier,
                "status": "missing_output_price",
                "class_spec_score": entry["class_spec_score"],
                "likely_spec_count": entry["likely_spec_count"],
                "situational_spec_count": entry["situational_spec_count"],
                "material_cost": None,
                "sell_price": None,
                "net_sell_after_cut": None,
                "profit": None,
                "roi": None,
                "available": None,
                "recommended_quantity": 0,
                "material_sources": [],
                "material_source_summary": "",
                "reason": "Output item not found in AH snapshot"
            })
            continue

        sell_price = snapshot[item_name]["price"]
        available = snapshot[item_name]["available"]
        mat_cost, material_sources, material_source_summary = compute_material_cost_details(
            recipe_entry.get("reagents"),
            snapshot,
            crafting_data,
            recipe_lookup,
            cost_cache,
        )
        net_sell = math.floor(sell_price * (1 - AH_CUT))

        if mat_cost is None:
            results.append({
                "rank": rank,
                "item": item_name,
                "category": category,
                "tier": tier,
                "status": "missing_reagent_price",
                "class_spec_score": entry["class_spec_score"],
                "likely_spec_count": entry["likely_spec_count"],
                "situational_spec_count": entry["situational_spec_count"],
                "material_cost": None,
                "sell_price": sell_price,
                "net_sell_after_cut": net_sell,
                "profit": None,
                "roi": None,
                "available": available,
                "recommended_quantity": 0,
                "material_sources": [],
                "material_source_summary": "",
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
            "class_spec_score": entry["class_spec_score"],
            "likely_spec_count": entry["likely_spec_count"],
            "situational_spec_count": entry["situational_spec_count"],
            "material_cost": mat_cost,
            "sell_price": sell_price,
            "net_sell_after_cut": net_sell,
            "profit": profit,
            "roi": round(roi, 4) if roi is not None else None,
            "available": available,
            "recommended_quantity": qty,
            "material_sources": material_sources,
            "material_source_summary": material_source_summary,
            "reason": entry.get("reason", "")
        })

    # Sort by:
    # 1. profitable first
    # 2. higher profit first
    # 3. better ROI next
    # 4. stronger class/spec demand next
    # 5. lower competition next
    # 6. better curated rank next
    results.sort(
        key=lambda row: (
            0 if row["status"] == "ok" else 1,
            -(row["profit"] if row["profit"] is not None else -10**12),
            -(row["roi"] if row["roi"] is not None else -10**12),
            -row.get("class_spec_score", 0),
            row["available"] if row["available"] is not None else 10**12,
            row["rank"],
            row["item"],
        )
    )
    return results

def write_outputs(results: List[Dict[str, Any]], output_json: str, output_csv: str) -> None:
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    with open(output_csv, "w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "rank", "item", "category", "tier", "status",
            "class_spec_score", "likely_spec_count", "situational_spec_count",
            "material_cost", "sell_price", "net_sell_after_cut",
            "profit", "roi", "available", "recommended_quantity",
            "material_source_summary", "reason"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

def write_pricing_debug(
    output_path: str,
    imperial_silk_cost: Optional[Dict[str, Any]],
) -> None:
    payload = {"imperial_silk": None}
    if imperial_silk_cost is not None:
        payload["imperial_silk"] = {
            "unit_cost": imperial_silk_cost["unit_cost"],
            "unit_cost_readable": copper_to_gold(imperial_silk_cost["unit_cost"]),
            "source_type": imperial_silk_cost["source_type"],
            "source_summary": imperial_silk_cost["source_summary"],
            "source_detail": imperial_silk_cost["source_detail"],
            "chain": imperial_silk_cost["chain"],
        }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

def print_top(results: List[Dict[str, Any]], limit: Optional[int] = None) -> None:
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
            f" | demand={row['class_spec_score']}"
            f" | avail={row['available']}"
            f" | craft={row['recommended_quantity']}"
        )
        if row.get("material_source_summary"):
            print(f"    chain={row['material_source_summary']}")
        if limit is not None and shown >= limit:
            break

    print("\n=== SKIP / INCOMPLETE ===")
    for row in results:
        if row["status"] == "ok":
            continue
        print(
            f"{row['rank']:>2}. {row['item']} | status={row['status']} | reason={row['reason']}"
        )
        if row.get("material_source_summary"):
            print(f"    chain={row['material_source_summary']}")

def main() -> None:
    snapshot = load_snapshot(SNAPSHOT_CSV)
    class_spec_data = load_json(CLASS_SPEC_JSON)
    crafting_data = load_json(CRAFTING_JSON)
    planner_entries = build_planner_entries(class_spec_data)
    results = build_plan(snapshot, planner_entries, crafting_data)
    write_outputs(results, OUTPUT_JSON, OUTPUT_CSV)
    imperial_silk_cost = resolve_unit_cost(
        "Imperial Silk",
        snapshot,
        crafting_data,
        build_recipe_lookup(crafting_data),
        {},
        set(),
    )
    write_pricing_debug(PRICING_DEBUG_JSON, imperial_silk_cost)
    if imperial_silk_cost is not None:
        print("\n=== IMPERIAL SILK COST ===")
        print(
            f"Imperial Silk: {copper_to_gold(imperial_silk_cost['unit_cost'])}"
            f" via {imperial_silk_cost['chain']}"
        )
    print_top(results)
    print(f"\nSaved: {OUTPUT_JSON}")
    print(f"Saved: {OUTPUT_CSV}")
    print(f"Saved: {PRICING_DEBUG_JSON}")

if __name__ == "__main__":
    main()
