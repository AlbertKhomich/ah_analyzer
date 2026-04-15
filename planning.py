import math
from typing import Any, Dict, List, Optional, Set, Tuple

from pricing import PricingContext, normalize_name, resolve_recipe_craft_cost


BASE_STOCK = {
    "glyph": {"S": 2, "A": 1, "B": 1, "C": 0},
    "potion": {"S": 40, "A": 25, "B": 12, "C": 0},
    "flask": {"S": 20, "A": 12, "B": 8, "C": 0},
    "food": {"S": 25, "A": 18, "B": 10, "C": 5},
    "feast": {"S": 4, "A": 2, "B": 1, "C": 0},
    "shoulder_inscription": {"S": 5, "A": 4, "B": 2, "C": 0},
    "alchemy_transmutation": {"S": 2, "A": 1, "B": 1, "C": 0},
    "alchemy_mount": {"S": 1, "A": 1, "B": 0, "C": 0},
    "inscription_card": {"S": 5, "A": 3, "B": 1, "C": 0},
    "tailoring_spellthread": {"S": 5, "A": 3, "B": 1, "C": 0},
    "tailoring_bag": {"S": 2, "A": 1, "B": 1, "C": 0},
    "tailoring_epic": {"S": 1, "A": 1, "B": 1, "C": 0},
    "tailoring_pvp": {"S": 1, "A": 1, "B": 1, "C": 0},
}

TIER_PRIORITY = {"S": 4, "A": 3, "B": 2, "C": 1}


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


def recommended_quantity(
    category: str,
    tier: str,
    rank: int,
    profit: Optional[int],
    roi: Optional[float],
    available: int,
) -> int:
    base = BASE_STOCK.get(category, {"S": 1, "A": 1, "B": 0, "C": 0}).get(tier, 0)

    if base == 0 or profit is None or profit <= 0:
        return 0

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

    if rank <= 10:
        multiplier *= 1.2
    elif rank <= 20:
        multiplier *= 1.1

    qty = max(0, math.floor(base * multiplier))

    if category == "glyph":
        qty = min(qty, 3)

    if category in {"tailoring_bag", "tailoring_epic", "tailoring_pvp"}:
        qty = min(qty, 2)

    return qty


def snapshot_sale_context(
    item_name: str,
    snapshot: Dict[str, Dict[str, Any]],
    auction_house_cut: float,
) -> Dict[str, Optional[int]]:
    if item_name not in snapshot:
        return {
            "sell_price": None,
            "available": None,
            "net_sell_after_cut": None,
        }

    sell_price = snapshot[item_name]["price"]
    return {
        "sell_price": sell_price,
        "available": snapshot[item_name]["available"],
        "net_sell_after_cut": math.floor(sell_price * (1 - auction_house_cut)),
    }


def make_plan_row(
    entry: Dict[str, Any],
    status: str,
    reason: str,
    *,
    sell_price: Optional[int] = None,
    net_sell_after_cut: Optional[int] = None,
    available: Optional[int] = None,
    material_cost: Optional[int] = None,
    profit: Optional[int] = None,
    roi: Optional[float] = None,
    recommended_quantity: int = 0,
    material_sources: Optional[List[Dict[str, Any]]] = None,
    material_source_summary: str = "",
    material_cost_detail: str = "",
) -> Dict[str, Any]:
    return {
        "rank": entry["rank"],
        "item": entry["item"],
        "category": entry["category"],
        "tier": entry["tier"],
        "status": status,
        "class_spec_score": entry["class_spec_score"],
        "likely_spec_count": entry["likely_spec_count"],
        "situational_spec_count": entry["situational_spec_count"],
        "material_cost": material_cost,
        "sell_price": sell_price,
        "net_sell_after_cut": net_sell_after_cut,
        "profit": profit,
        "roi": round(roi, 4) if roi is not None else None,
        "available": available,
        "recommended_quantity": recommended_quantity,
        "material_sources": material_sources or [],
        "material_source_summary": material_source_summary,
        "material_cost_detail": material_cost_detail,
        "reason": reason,
    }


def plan_sort_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    missing_numeric = 10**12
    return (
        0 if row["status"] == "ok" else 1,
        -(row["profit"] if row["profit"] is not None else -missing_numeric),
        -(row["roi"] if row["roi"] is not None else -missing_numeric),
        -row.get("class_spec_score", 0),
        row["available"] if row["available"] is not None else missing_numeric,
        row["rank"],
        row["item"],
    )


def build_plan(
    snapshot: Dict[str, Dict[str, Any]],
    planner_entries: List[Dict[str, Any]],
    crafting_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    pricing_context = PricingContext(snapshot=snapshot, crafting_data=crafting_data)

    for entry in planner_entries:
        item_name = entry["item"]
        recipe_entry = pricing_context.recipe_lookup.get(item_name)
        sale_context = snapshot_sale_context(
            item_name,
            snapshot,
            pricing_context.auction_house_cut,
        )

        if recipe_entry is None:
            results.append(
                make_plan_row(
                    entry,
                    status="missing_recipe_data",
                    reason="Recipe entry not found in crafting data",
                    sell_price=sale_context["sell_price"],
                    net_sell_after_cut=sale_context["net_sell_after_cut"],
                    available=sale_context["available"],
                )
            )
            continue

        if sale_context["sell_price"] is None or sale_context["available"] is None:
            results.append(
                make_plan_row(
                    entry,
                    status="missing_output_price",
                    reason="Output item not found in AH snapshot",
                )
            )
            continue

        crafted_cost = resolve_recipe_craft_cost(
            pricing_context,
            recipe_entry,
        )

        if crafted_cost is None:
            results.append(
                make_plan_row(
                    entry,
                    status="missing_reagent_price",
                    reason="At least one reagent price missing",
                    sell_price=sale_context["sell_price"],
                    net_sell_after_cut=sale_context["net_sell_after_cut"],
                    available=sale_context["available"],
                )
            )
            continue

        mat_cost = crafted_cost.unit_cost
        sell_price = sale_context["sell_price"]
        net_sell_after_cut = sale_context["net_sell_after_cut"]
        available = sale_context["available"]
        assert sell_price is not None
        assert net_sell_after_cut is not None
        assert available is not None

        profit = net_sell_after_cut - mat_cost
        roi = profit / mat_cost if mat_cost > 0 else None
        qty = recommended_quantity(
            entry["category"],
            entry["tier"],
            entry["rank"],
            profit,
            roi,
            available,
        )

        results.append(
            make_plan_row(
                entry,
                status="ok" if profit > 0 else "skip",
                reason=entry.get("reason", ""),
                sell_price=sell_price,
                net_sell_after_cut=net_sell_after_cut,
                available=available,
                material_cost=mat_cost,
                profit=profit,
                roi=roi,
                recommended_quantity=qty,
                material_sources=[component.to_dict() for component in crafted_cost.components],
                material_source_summary=crafted_cost.component_chain,
                material_cost_detail=crafted_cost.cost_detail,
            )
        )

    results.sort(key=plan_sort_key)
    return results
