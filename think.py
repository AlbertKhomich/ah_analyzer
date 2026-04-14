import csv
import json
from typing import Any, Dict, List, Optional

from planner_data import PLANNER_JSON_FILES, load_json, load_planner_data
from planning import build_plan, build_planner_entries
from pricing import (
    CostOption,
    PricingContext,
    copper_to_gold,
    get_pricing_rules,
    load_snapshot,
    resolve_unit_cost,
)


SNAPSHOT_CSV = "ah_snapshot.csv"
CRAFTING_JSON = "crafting_data.json"
OUTPUT_JSON = "craft_plan.json"
OUTPUT_CSV = "craft_plan.csv"
PRICING_DEBUG_JSON = "pricing_debug.json"


def write_outputs(results: List[Dict[str, Any]], output_json: str, output_csv: str) -> None:
    with open(output_json, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)

    with open(output_csv, "w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "rank", "item", "category", "tier", "status",
            "class_spec_score", "likely_spec_count", "situational_spec_count",
            "material_cost", "sell_price", "net_sell_after_cut",
            "profit", "roi", "available", "recommended_quantity",
            "material_source_summary", "material_cost_detail", "reason",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


def write_pricing_debug(
    output_path: str,
    imperial_silk_cost: Optional[CostOption],
) -> None:
    payload = {"imperial_silk": None}
    if imperial_silk_cost is not None:
        payload["imperial_silk"] = {
            "unit_cost": imperial_silk_cost.unit_cost,
            "unit_cost_readable": copper_to_gold(imperial_silk_cost.unit_cost),
            "source_type": imperial_silk_cost.source_type,
            "source_summary": imperial_silk_cost.source_summary,
            "source_detail": imperial_silk_cost.source_detail,
            "chain": imperial_silk_cost.chain,
        }
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


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
        if row.get("material_cost_detail"):
            print(f"    cost={row['material_cost_detail']}")
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
    crafting_data = load_json(CRAFTING_JSON)
    pricing_rules = get_pricing_rules(crafting_data)
    snapshot = load_snapshot(SNAPSHOT_CSV, pricing_rules.get("name_aliases"))
    class_spec_data = load_planner_data(PLANNER_JSON_FILES)
    pricing_context = PricingContext(snapshot=snapshot, crafting_data=crafting_data)
    planner_entries = build_planner_entries(class_spec_data)
    results = build_plan(snapshot, planner_entries, crafting_data)
    write_outputs(results, OUTPUT_JSON, OUTPUT_CSV)

    imperial_silk_cost = resolve_unit_cost(pricing_context, "Imperial Silk")
    write_pricing_debug(PRICING_DEBUG_JSON, imperial_silk_cost)

    if imperial_silk_cost is not None:
        print("\n=== IMPERIAL SILK COST ===")
        print(
            f"Imperial Silk: {copper_to_gold(imperial_silk_cost.unit_cost)}"
            f" via {imperial_silk_cost.chain}"
        )

    print_top(results)
    print(f"\nSaved: {OUTPUT_JSON}")
    print(f"Saved: {OUTPUT_CSV}")
    print(f"Saved: {PRICING_DEBUG_JSON}")


if __name__ == "__main__":
    main()
