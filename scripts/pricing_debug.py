import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import _bootstrap  # noqa: F401

from ah_trading.paths import AH_SNAPSHOT_CSV, CRAFTING_JSON, OUTPUT_DIR
from ah_trading.planner_data import load_json
from ah_trading.pricing import (
    PricingContext,
    build_pricing_debug_entry,
    get_pricing_rules,
    load_snapshot,
)


SNAPSHOT_CSV = AH_SNAPSHOT_CSV
OUTPUT_JSON = OUTPUT_DIR / "pricing_debug.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect resolved pricing paths for specific items."
    )
    parser.add_argument(
        "--item",
        action="append",
        default=[],
        help="Item to inspect. Repeat this flag to debug multiple items.",
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_JSON,
        help="Path to save the pricing debug JSON report.",
    )
    parser.add_argument(
        "--snapshot",
        default=SNAPSHOT_CSV,
        help="AH snapshot CSV to use for price resolution.",
    )
    parser.add_argument(
        "--crafting-data",
        default=CRAFTING_JSON,
        help="Crafting data JSON to use for recipe and pricing rules.",
    )
    return parser.parse_args()


def build_pricing_debug_report(
    crafting_data: Dict[str, Any],
    snapshot: Dict[str, Dict[str, Any]],
    item_names: List[str],
) -> Dict[str, Any]:
    pricing_context = PricingContext(snapshot=snapshot, crafting_data=crafting_data)
    return {
        "items": [
            build_pricing_debug_entry(pricing_context, item_name)
            for item_name in item_names
        ]
    }


def write_report(output_path: str, payload: Dict[str, Any]) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def print_report(payload: Dict[str, Any]) -> None:
    for entry in payload.get("items", []):
        item_name = entry["item"]
        resolved = entry.get("resolved_cost")
        if resolved is None:
            print(f"{item_name}: unresolved")
            continue

        print(
            f"{item_name}: {resolved['unit_cost_readable']}"
            f" via {resolved['chain']}"
        )


def main() -> None:
    args = parse_args()
    if not args.item:
        raise ValueError("Provide at least one --item to inspect.")

    crafting_data = load_json(args.crafting_data)
    pricing_rules = get_pricing_rules(crafting_data)
    snapshot = load_snapshot(args.snapshot, pricing_rules.get("name_aliases"))
    payload = build_pricing_debug_report(crafting_data, snapshot, args.item)
    write_report(args.output, payload)
    print_report(payload)
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
