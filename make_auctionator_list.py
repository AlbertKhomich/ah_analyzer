import argparse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from planner_data import (
    PLANNER_JSON_FILES,
    load_json,
    load_planner_data,
    merge_active_event_entries,
)
from pricing import PricingContext, get_pricing_rules, load_snapshot, resolve_unit_cost


CRAFTING_JSON = "crafting_data.json"
SNAPSHOT_CSV = "ah_snapshot.csv"
ENTRY_SUFFIX = '";;0;0;0;0;0;0;0;0;;#;;'


def build_craft_lookup(crafting_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        entry["item"]: entry
        for entry in crafting_data.get("craft_targets", [])
    }


def get_nested_item(
    container: Dict[str, Dict[str, Any]],
    item_name: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    for key, value in container.items():
        if key.lower() == item_name.lower():
            return key, value
    return None


def add_unique(target: List[str], seen: Set[str], item_name: str) -> None:
    normalized = item_name.casefold()
    if normalized in seen:
        return
    seen.add(normalized)
    target.append(item_name)


def extract_named_craft_option(chain: str) -> Optional[str]:
    if not chain.startswith("craft["):
        return None
    end = chain.find("](")
    if end == -1:
        return None
    return chain[len("craft["):end]


def expand_reagent_names(
    reagent_names: Iterable[str],
    ordered_items: List[str],
    seen: Set[str],
    craft_lookup: Dict[str, Dict[str, Any]],
    crafting_data: Dict[str, Any],
    pricing_context: PricingContext,
    visited_nodes: Set[str],
) -> None:
    for reagent_name in reagent_names:
        expand_item_chain(
            item_name=reagent_name,
            ordered_items=ordered_items,
            seen=seen,
            craft_lookup=craft_lookup,
            crafting_data=crafting_data,
            pricing_context=pricing_context,
            visited_nodes=visited_nodes,
            prefer_recipe_entry=False,
        )


def expand_static_support_chain(
    item_name: str,
    ordered_items: List[str],
    seen: Set[str],
    craft_lookup: Dict[str, Dict[str, Any]],
    crafting_data: Dict[str, Any],
    pricing_context: PricingContext,
    visited_nodes: Set[str],
) -> None:
    support = crafting_data.get("supporting_recipes", {})

    tailoring_entry = get_nested_item(support.get("tailoring_subcrafts", {}), item_name)
    if tailoring_entry is not None:
        _, recipe_data = tailoring_entry
        expand_reagent_names(
            (reagent["item"] for reagent in recipe_data.get("crafted_from", [])),
            ordered_items,
            seen,
            craft_lookup,
            crafting_data,
            pricing_context,
            visited_nodes,
        )
        for option_data in recipe_data.get("crafted_from_options", []):
            expand_reagent_names(
                (reagent["item"] for reagent in option_data.get("crafted_from", [])),
                ordered_items,
                seen,
                craft_lookup,
                crafting_data,
                pricing_context,
                visited_nodes,
            )
        for alt_item in recipe_data.get("alternative_sources", []):
            add_unique(ordered_items, seen, alt_item)

    inscription = support.get("inscription", {})
    ink_entry = get_nested_item(inscription.get("inks", {}), item_name)
    if ink_entry is not None:
        _, ink_data = ink_entry
        expand_reagent_names(
            (reagent["item"] for reagent in ink_data.get("crafted_from", [])),
            ordered_items,
            seen,
            craft_lookup,
            crafting_data,
            pricing_context,
            visited_nodes,
        )

    trade_entry = get_nested_item(inscription.get("vendor_trades", {}), item_name)
    if trade_entry is not None:
        _, trade_data = trade_entry
        expand_reagent_names(
            (reagent["item"] for reagent in trade_data.get("cost", [])),
            ordered_items,
            seen,
            craft_lookup,
            crafting_data,
            pricing_context,
            visited_nodes,
        )

    pigment_entry = get_nested_item(
        support.get("milling", {}).get("pigments", {}),
        item_name,
    )
    if pigment_entry is not None:
        _, pigment_data = pigment_entry
        for herb_name in pigment_data.get("milled_from", []):
            add_unique(ordered_items, seen, herb_name)


def expand_selected_source_chain(
    item_name: str,
    ordered_items: List[str],
    seen: Set[str],
    craft_lookup: Dict[str, Dict[str, Any]],
    crafting_data: Dict[str, Any],
    pricing_context: PricingContext,
    visited_nodes: Set[str],
) -> bool:
    resolved = resolve_unit_cost(pricing_context, item_name)
    if resolved is None:
        return False

    support = crafting_data.get("supporting_recipes", {})

    if resolved.source_type == "vendor_trade":
        inscription = support.get("inscription", {})
        trade_entry = get_nested_item(inscription.get("vendor_trades", {}), item_name)
        if trade_entry is None:
            return True
        _, trade_data = trade_entry
        expand_reagent_names(
            (reagent["item"] for reagent in trade_data.get("cost", [])),
            ordered_items,
            seen,
            craft_lookup,
            crafting_data,
            pricing_context,
            visited_nodes,
        )
        return True

    if resolved.source_type == "milling":
        if resolved.chain.startswith("mill:"):
            herb_name = resolved.chain[len("mill:"):]
            expand_reagent_names(
                [herb_name],
                ordered_items,
                seen,
                craft_lookup,
                crafting_data,
                pricing_context,
                visited_nodes,
            )
        return True

    if resolved.source_type != "crafted":
        return True

    tailoring_entry = get_nested_item(support.get("tailoring_subcrafts", {}), item_name)
    chosen_option = extract_named_craft_option(resolved.chain)
    if tailoring_entry is not None:
        _, recipe_data = tailoring_entry
        if recipe_data.get("shopping_list_mode") == "all_options":
            for option_data in recipe_data.get("crafted_from_options", []):
                expand_reagent_names(
                    (reagent["item"] for reagent in option_data.get("crafted_from", [])),
                    ordered_items,
                    seen,
                    craft_lookup,
                    crafting_data,
                    pricing_context,
                    visited_nodes,
                )
            return True

    if tailoring_entry is not None and chosen_option is not None:
        _, recipe_data = tailoring_entry
        for option_data in recipe_data.get("crafted_from_options", []):
            option_name = str(option_data.get("name") or option_data.get("label") or "").strip()
            if option_name != chosen_option:
                continue
            expand_reagent_names(
                (reagent["item"] for reagent in option_data.get("crafted_from", [])),
                ordered_items,
                seen,
                craft_lookup,
                crafting_data,
                pricing_context,
                visited_nodes,
            )
            return True

    craft_entry = craft_lookup.get(item_name)
    if craft_entry is not None:
        expand_reagent_names(
            (reagent["item"] for reagent in craft_entry.get("reagents") or []),
            ordered_items,
            seen,
            craft_lookup,
            crafting_data,
            pricing_context,
            visited_nodes,
        )
        return True

    inscription = support.get("inscription", {})
    ink_entry = get_nested_item(inscription.get("inks", {}), item_name)
    if ink_entry is not None:
        _, ink_data = ink_entry
        expand_reagent_names(
            (reagent["item"] for reagent in ink_data.get("crafted_from", [])),
            ordered_items,
            seen,
            craft_lookup,
            crafting_data,
            pricing_context,
            visited_nodes,
        )
        return True

    if tailoring_entry is not None:
        _, recipe_data = tailoring_entry
        expand_reagent_names(
            (reagent["item"] for reagent in recipe_data.get("crafted_from", [])),
            ordered_items,
            seen,
            craft_lookup,
            crafting_data,
            pricing_context,
            visited_nodes,
        )
        return True

    return True


def get_seed_items(
    class_spec_data: Dict[str, Any],
    class_name: Optional[str],
    spec_name: Optional[str],
    group_name: Optional[str],
    explicit_items: List[str],
    include_situational: bool,
    include_all: bool,
) -> List[str]:
    seeds: List[str] = []
    seen: Set[str] = set()

    if include_all:
        for item_name in class_spec_data.get("item_index", {}):
            add_unique(seeds, seen, item_name)

    if explicit_items:
        for item in explicit_items:
            add_unique(seeds, seen, item)

    if class_name and spec_name:
        class_block = class_spec_data["classes"].get(class_name)
        if class_block is None:
            raise ValueError(f"Unknown class: {class_name}")
        spec_block = class_block.get(spec_name)
        if spec_block is None:
            raise ValueError(f"Unknown spec '{spec_name}' for class '{class_name}'")

        for item in spec_block.get("likely_items", []):
            add_unique(seeds, seen, item)
        if include_situational:
            for item in spec_block.get("situational_items", []):
                add_unique(seeds, seen, item)

    if group_name:
        group_block = class_spec_data["shared_item_groups"].get(group_name)
        if group_block is None:
            raise ValueError(f"Unknown shared group: {group_name}")
        for item in group_block.get("items", []):
            add_unique(seeds, seen, item)

    if not seeds:
        raise ValueError(
            "No seed items selected. Provide --class and --spec, --group, or at least one --item."
        )

    return seeds


def expand_item_chain(
    item_name: str,
    ordered_items: List[str],
    seen: Set[str],
    craft_lookup: Dict[str, Dict[str, Any]],
    crafting_data: Dict[str, Any],
    pricing_context: PricingContext,
    visited_nodes: Set[str],
    prefer_recipe_entry: bool = True,
) -> None:
    add_unique(ordered_items, seen, item_name)

    normalized = item_name.casefold()
    if normalized in visited_nodes:
        return
    visited_nodes.add(normalized)

    craft_entry = craft_lookup.get(item_name)
    if prefer_recipe_entry and craft_entry is not None:
        expand_reagent_names(
            (reagent["item"] for reagent in craft_entry.get("reagents") or []),
            ordered_items,
            seen,
            craft_lookup,
            crafting_data,
            pricing_context,
            visited_nodes,
        )
        return

    if expand_selected_source_chain(
        item_name,
        ordered_items,
        seen,
        craft_lookup,
        crafting_data,
        pricing_context,
        visited_nodes,
    ):
        return

    expand_static_support_chain(
        item_name,
        ordered_items,
        seen,
        craft_lookup,
        crafting_data,
        pricing_context,
        visited_nodes,
    )


def pack_auctionator_list(list_name: str, items: Iterable[str]) -> str:
    entries = [f'^"{item}{ENTRY_SUFFIX}' for item in items]
    return f"{list_name}{''.join(entries)}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an Auctionator import string from class/spec demand items and crafting chains."
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Auctionator shopping list name prefix before the packed entries.",
    )
    parser.add_argument(
        "--class",
        dest="class_name",
        help="Class name from class_spec_items.json, for example 'Priest'.",
    )
    parser.add_argument(
        "--spec",
        dest="spec_name",
        help="Spec name from class_spec_items.json, for example 'Discipline'.",
    )
    parser.add_argument(
        "--group",
        help="Shared item group from class_spec_items.json, for example 'intellect_consumables'.",
    )
    parser.add_argument(
        "--item",
        action="append",
        default=[],
        help="Explicit item to include. Repeat this flag to add more items.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include every craft target from class_spec_items.json before expanding the full reagent chain.",
    )
    parser.add_argument(
        "--include-situational",
        action="store_true",
        help="When using --class and --spec, include situational_items too.",
    )
    parser.add_argument(
        "--output",
        help="Optional file path to save the packed Auctionator import string.",
    )
    parser.add_argument(
        "--print-items",
        action="store_true",
        help="Print the expanded ordered item list after the packed string.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    crafting_data = load_json(CRAFTING_JSON)
    class_spec_data = merge_active_event_entries(
        load_planner_data(PLANNER_JSON_FILES),
        crafting_data,
    )
    craft_lookup = build_craft_lookup(crafting_data)
    pricing_rules = get_pricing_rules(crafting_data)
    snapshot = load_snapshot(SNAPSHOT_CSV, pricing_rules.get("name_aliases"))
    pricing_context = PricingContext(snapshot=snapshot, crafting_data=crafting_data)

    seeds = get_seed_items(
        class_spec_data=class_spec_data,
        class_name=args.class_name,
        spec_name=args.spec_name,
        group_name=args.group,
        explicit_items=args.item,
        include_situational=args.include_situational,
        include_all=args.all,
    )

    ordered_items: List[str] = []
    seen: Set[str] = set()
    for item_name in seeds:
        expand_item_chain(
            item_name=item_name,
            ordered_items=ordered_items,
            seen=seen,
            craft_lookup=craft_lookup,
            crafting_data=crafting_data,
            pricing_context=pricing_context,
            visited_nodes=set(),
        )

    packed = pack_auctionator_list(args.name, ordered_items)

    if args.output:
        Path(args.output).write_text(packed + "\n", encoding="utf-8")

    print(packed)

    if args.print_items:
        print()
        for item_name in ordered_items:
            print(item_name)


if __name__ == "__main__":
    main()
