import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import _bootstrap  # noqa: F401

try:
    from ah_price_heatmap import plot_price_heatmap
except ModuleNotFoundError:
    plot_price_heatmap = None
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
    normalize_name,
    resolve_unit_cost,
)


SNAPSHOT_CSV = AH_SNAPSHOT_CSV
OUTPUT_JSON = OUTPUT_DIR / "craft_plan.json"
OUTPUT_CSV = OUTPUT_DIR / "craft_plan.csv"
OUTPUT_HEATMAP = OUTPUT_DIR / "snapshot.png"
CONSOLE_PRICING_HIGHLIGHTS = [
    "Imperial Silk",
]


def format_price_delta(current_price: int, average_price: float) -> Optional[str]:
    if average_price <= 0:
        return None

    percentage_difference = ((current_price - average_price) / average_price) * 100.0
    rounded_difference = round(percentage_difference)
    if rounded_difference > 0:
        return f"+{rounded_difference}%"
    return f"{rounded_difference}%"


def build_average_price_lookup(
    history_dir: Path,
    name_aliases: Optional[Dict[str, str]] = None,
) -> Dict[str, float]:
    price_totals: Dict[str, int] = {}
    price_counts: Dict[str, int] = {}

    for csv_path in sorted(history_dir.glob("*_ah_snapshot.csv")):
        snapshot = load_snapshot(str(csv_path), name_aliases)
        for item_name, item_data in snapshot.items():
            price_totals[item_name] = price_totals.get(item_name, 0) + item_data["price"]
            price_counts[item_name] = price_counts.get(item_name, 0) + 1

    return {
        item_name: price_totals[item_name] / price_counts[item_name]
        for item_name in price_totals
        if price_counts[item_name] > 0
    }


def build_current_average_delta_lookup(
    snapshot: Dict[str, Dict[str, Any]],
    history_dir: Path,
    name_aliases: Optional[Dict[str, str]] = None,
    pricing_context: Optional[PricingContext] = None,
    displayed_results: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, str]:
    average_prices = build_average_price_lookup(history_dir, name_aliases)
    item_names = set(snapshot)
    if displayed_results is not None:
        item_names.update(
            collect_display_item_names(
                displayed_results,
                average_prices.keys(),
                name_aliases,
            )
        )

    deltas: Dict[str, str] = {}

    for item_name in item_names:
        average_price = average_prices.get(item_name)
        if average_price is None:
            continue

        current_price = resolve_current_price_for_delta(
            item_name,
            snapshot,
            pricing_context,
        )
        if current_price is None:
            continue

        delta = format_price_delta(current_price, average_price)
        if delta is not None:
            deltas[item_name] = delta

    return deltas


def collect_display_item_names(
    results: List[Dict[str, Any]],
    candidate_item_names: Iterable[str],
    name_aliases: Optional[Dict[str, str]] = None,
) -> Set[str]:
    displayed_item_names = {
        normalize_name(str(row["item"]), name_aliases)
        for row in results
    }
    source_chains = [
        str(row.get("material_source_summary") or "")
        for row in results
    ]

    for item_name in sorted(candidate_item_names, key=len, reverse=True):
        normalized_item_name = normalize_name(item_name, name_aliases)
        if any(normalized_item_name in source_chain for source_chain in source_chains):
            displayed_item_names.add(normalized_item_name)

    return displayed_item_names


def resolve_current_price_for_delta(
    item_name: str,
    snapshot: Dict[str, Dict[str, Any]],
    pricing_context: Optional[PricingContext] = None,
) -> Optional[int]:
    if item_name in snapshot:
        return snapshot[item_name]["price"]

    if pricing_context is None:
        return None

    resolved = resolve_unit_cost(pricing_context, item_name)
    if resolved is None:
        return None
    return resolved.unit_cost


def decorate_item_name(item_name: str, price_deltas: Dict[str, str]) -> str:
    delta = price_deltas.get(item_name)
    if delta is None:
        return item_name
    return f"{item_name} ({delta})"


def decorate_source_chain(
    source_chain: str,
    price_deltas: Dict[str, str],
    name_aliases: Optional[Dict[str, str]] = None,
) -> str:
    decorated_chain = source_chain
    for item_name in sorted(price_deltas, key=len, reverse=True):
        normalized_item_name = normalize_name(item_name, name_aliases)
        for source_prefix in ("AH", "craft", "trade", "mill"):
            decorated_chain = decorated_chain.replace(
                f"{normalized_item_name}->{source_prefix}",
                f"{normalized_item_name} ({price_deltas[item_name]})->{source_prefix}",
            )
        decorated_chain = decorated_chain.replace(
            f"mill:{normalized_item_name}",
            f"mill:{normalized_item_name} ({price_deltas[item_name]})",
        )
    return decorated_chain


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


def print_top(
    results: List[Dict[str, Any]],
    limit: Optional[int] = None,
    price_deltas: Optional[Dict[str, str]] = None,
    name_aliases: Optional[Dict[str, str]] = None,
) -> None:
    price_deltas = price_deltas or {}
    print("\n=== TOP CRAFTS ===")
    shown = 0
    for row in results:
        if row["status"] != "ok":
            continue
        shown += 1
        print(
            f"{row['rank']:>2}. {decorate_item_name(row['item'], price_deltas)}"
            f" | profit={copper_to_gold(row['profit'])}"
            f" | roi={row['roi']:.2%}"
            f" | ah={copper_to_gold(row['sell_price'])}"
            f" | mats={copper_to_gold(row['material_cost'])}"
            f" | demand={row['class_spec_score']}"
            f" | avail={row['available']}"
            f" | craft={row['recommended_quantity']}"
        )
        if row.get("material_source_summary"):
            source_chain = decorate_source_chain(
                row["material_source_summary"],
                price_deltas,
                name_aliases,
            )
            print(f"    chain={source_chain}")
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
    name_aliases = pricing_rules.get("name_aliases")
    snapshot = load_snapshot(SNAPSHOT_CSV, name_aliases)
    pricing_context = PricingContext(snapshot=snapshot, crafting_data=crafting_data)
    class_spec_data = merge_active_event_entries(
        load_planner_data(PLANNER_JSON_FILES),
        crafting_data,
    )
    planner_entries = build_planner_entries(class_spec_data)
    results = build_plan(snapshot, planner_entries, crafting_data)
    price_deltas = build_current_average_delta_lookup(
        snapshot,
        HISTORY_DIR,
        name_aliases,
        pricing_context,
        results,
    )
    write_outputs(results, OUTPUT_JSON, OUTPUT_CSV)
    if plot_price_heatmap is not None:
        plot_price_heatmap(str(HISTORY_DIR), output_path=str(OUTPUT_HEATMAP))
    else:
        print("\nSkipped heatmap: matplotlib is not installed.")

    print_pricing_highlights(pricing_context, CONSOLE_PRICING_HIGHLIGHTS)
    print_top(results, price_deltas=price_deltas, name_aliases=name_aliases)
    print(f"\nSaved: {OUTPUT_JSON}")
    print(f"Saved: {OUTPUT_CSV}")
    if plot_price_heatmap is not None:
        print(f"Saved: {OUTPUT_HEATMAP}")


if __name__ == "__main__":
    main()
