import csv
import json
from typing import Any, Dict, List, Optional

import _bootstrap  # noqa: F401

from ah_price_heatmap import plot_price_heatmap
from ah_trading.paths import AH_SNAPSHOT_CSV, CRAFTING_JSON, HISTORY_DIR, OUTPUT_DIR
from ah_trading.planner_data import (
    PLANNER_JSON_FILES,
    load_json,
    load_planner_data,
    merge_active_event_entries,
)
from ah_trading.planning import build_plan, build_planner_entries
from ah_trading.pricing import (
    PricingContext,
    build_pricing_debug_entry,
    copper_to_gold,
    get_pricing_rules,
    load_snapshot,
)


SNAPSHOT_CSV = AH_SNAPSHOT_CSV
OUTPUT_JSON = OUTPUT_DIR / "craft_plan.json"
OUTPUT_CSV = OUTPUT_DIR / "craft_plan.csv"
OUTPUT_HEATMAP = OUTPUT_DIR / "snapshot.png"
CONSOLE_PRICING_HIGHLIGHTS = [
    "Imperial Silk",
]


def write_outputs(results: List[Dict[str, Any]], output_json: str, output_csv: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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


def print_pricing_highlights(
    pricing_context: PricingContext,
    item_names: List[str],
) -> None:
    if not item_names:
        return

    print("\n=== PRICING HIGHLIGHTS ===")
    for item_name in item_names:
        entry = build_pricing_debug_entry(pricing_context, item_name)
        resolved = entry.get("resolved_cost")
        if resolved is None:
            print(f"{entry['item']}: unresolved")
            continue

        print(
            f"{entry['item']}: {resolved['unit_cost_readable']}"
            f" via {resolved['chain']}"
        )


def main() -> None:
    crafting_data = load_json(CRAFTING_JSON)
    pricing_rules = get_pricing_rules(crafting_data)
    snapshot = load_snapshot(SNAPSHOT_CSV, pricing_rules.get("name_aliases"))
    pricing_context = PricingContext(snapshot=snapshot, crafting_data=crafting_data)
    class_spec_data = merge_active_event_entries(
        load_planner_data(PLANNER_JSON_FILES),
        crafting_data,
    )
    planner_entries = build_planner_entries(class_spec_data)
    results = build_plan(snapshot, planner_entries, crafting_data)
    write_outputs(results, OUTPUT_JSON, OUTPUT_CSV)
    plot_price_heatmap(str(HISTORY_DIR), output_path=str(OUTPUT_HEATMAP))

    print_pricing_highlights(pricing_context, CONSOLE_PRICING_HIGHLIGHTS)
    print_top(results)
    print(f"\nSaved: {OUTPUT_JSON}")
    print(f"Saved: {OUTPUT_CSV}")
    print(f"Saved: {OUTPUT_HEATMAP}")


if __name__ == "__main__":
    main()
