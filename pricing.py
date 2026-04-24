import csv
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union


@dataclass(frozen=True)
class CostOption:
    item: str
    unit_cost: int
    source_type: str
    source_summary: str
    source_detail: str
    chain: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item": self.item,
            "unit_cost": self.unit_cost,
            "unit_cost_readable": copper_to_gold(self.unit_cost),
            "source_type": self.source_type,
            "source_summary": self.source_summary,
            "source_detail": self.source_detail,
            "chain": self.chain,
        }


@dataclass(frozen=True)
class ReagentComponent:
    item: str
    qty: Union[int, float]
    unit_cost: int
    total_cost: int
    source_type: str
    source_summary: str
    source_detail: str
    source_chain: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item": self.item,
            "qty": self.qty,
            "unit_cost": self.unit_cost,
            "total_cost": self.total_cost,
            "source_type": self.source_type,
            "source_summary": self.source_summary,
            "source_detail": self.source_detail,
            "source_chain": self.source_chain,
        }


@dataclass(frozen=True)
class ReagentResolution:
    total_cost: int
    chain: str
    components: List[ReagentComponent]


@dataclass(frozen=True)
class RecipeOutputProfile:
    base_output: float
    expected_output: float
    source: str


@dataclass(frozen=True)
class CraftedCost:
    unit_cost: int
    components: List[ReagentComponent]
    component_chain: str
    chain: str
    source_detail: str
    cost_detail: str


@dataclass
class PricingContext:
    snapshot: Dict[str, Dict[str, Any]]
    crafting_data: Dict[str, Any]
    cost_cache: Dict[str, CostOption] = field(default_factory=dict)
    pricing_rules: Dict[str, Any] = field(init=False)
    recipe_lookup: Dict[str, Dict[str, Any]] = field(init=False)
    support_recipes: Dict[str, Any] = field(init=False)
    inscription: Dict[str, Any] = field(init=False)
    tailoring_subcrafts: Dict[str, Any] = field(init=False)
    milling: Dict[str, Any] = field(init=False)
    alchemy_specializations: Dict[str, Any] = field(init=False)
    auction_house_cut: float = field(init=False)
    fallback_prices: Dict[str, int] = field(init=False)
    non_ah_reagent_prices: Dict[str, int] = field(init=False)
    force_crafted_cost_items: Set[str] = field(init=False)
    name_aliases: Dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.support_recipes = get_supporting_recipes(self.crafting_data)
        self.pricing_rules = get_pricing_rules(self.crafting_data)
        raw_name_aliases = self.pricing_rules.get("name_aliases", {})
        self.name_aliases = {
            str(name).strip(): str(canonical_name).strip()
            for name, canonical_name in raw_name_aliases.items()
        }
        self.recipe_lookup = build_recipe_lookup(self.crafting_data, self.name_aliases)
        self.inscription = self.support_recipes.get("inscription", {})
        self.tailoring_subcrafts = self.support_recipes.get("tailoring_subcrafts", {})
        self.milling = self.support_recipes.get("milling", {})
        self.alchemy_specializations = self.support_recipes.get("alchemy_specializations", {})
        self.auction_house_cut = float(self.pricing_rules.get("auction_house_cut", 0.05))
        self.fallback_prices = {
            normalize_name(name, self.name_aliases): int(price)
            for name, price in self.pricing_rules.get("fallback_prices", {}).items()
        }
        self.non_ah_reagent_prices = {
            normalize_name(name, self.name_aliases): int(price)
            for name, price in self.pricing_rules.get("non_ah_reagent_prices", {}).items()
        }
        self.force_crafted_cost_items = {
            normalize_name(name, self.name_aliases)
            for name in self.pricing_rules.get("force_crafted_cost_items", [])
        }


def normalize_name(name: str, aliases: Optional[Dict[str, str]] = None) -> str:
    name = name.strip()
    name = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()
    alias_map = aliases or {}
    return alias_map.get(name, name)


def copper_to_gold(copper: int) -> str:
    g = copper // 10000
    s = (copper % 10000) // 100
    c = copper % 100
    return f"{g}g {s}s {c}c"


def load_snapshot(
    csv_path: str,
    name_aliases: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    items: Dict[str, Dict[str, Any]] = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_name = row["Name"]
            name = normalize_name(raw_name, name_aliases)
            items[name] = {
                "raw_name": raw_name,
                "price": int(row["Price"]),
                "available": int(row["Available"]),
                "item_level": row.get("Item Level", ""),
            }
    return items


def get_recipe_entries(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "craft_targets" in data:
        return data["craft_targets"]
    if "priority_queue" in data:
        return data["priority_queue"]
    raise ValueError("Expected 'craft_targets' or 'priority_queue' in craft data JSON.")


def get_named_entry(
    entries: Dict[str, Any],
    name: str,
    name_aliases: Optional[Dict[str, str]] = None,
) -> Optional[Tuple[str, Any]]:
    normalized = normalize_name(name, name_aliases)
    for entry_name, entry_data in entries.items():
        if normalize_name(entry_name, name_aliases) == normalized:
            return entry_name, entry_data
    return None


def get_supporting_recipes(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("supporting_recipes", {})


def get_pricing_rules(data: Dict[str, Any]) -> Dict[str, Any]:
    return get_supporting_recipes(data).get("pricing", {})


def build_recipe_lookup(
    crafting_data: Dict[str, Any],
    name_aliases: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for entry in get_recipe_entries(crafting_data):
        lookup[normalize_name(entry["item"], name_aliases)] = entry
    return lookup


def format_qty(qty: float) -> str:
    if abs(qty - round(qty)) < 1e-9:
        return str(int(round(qty)))
    return f"{qty:.2f}".rstrip("0").rstrip(".")


def reagent_unit_price(ctx: PricingContext, name: str) -> Optional[int]:
    normalized = normalize_name(name, ctx.name_aliases)
    if normalized in ctx.snapshot:
        return ctx.snapshot[normalized]["price"]
    if normalized in ctx.non_ah_reagent_prices:
        return ctx.non_ah_reagent_prices[normalized]
    if normalized in ctx.fallback_prices:
        return ctx.fallback_prices[normalized]
    return None


def resolve_milling_rebate_value(
    ctx: PricingContext,
    pigment_name: str,
    herb_name: str,
) -> Tuple[float, str]:
    rebates = ctx.milling.get("rules", {}).get("expected_value_rebates", {})
    rebate_entry = get_named_entry(rebates, pigment_name, ctx.name_aliases)
    if rebate_entry is None:
        return 0.0, ""

    rebate_data = rebate_entry[1]
    rebate_item = normalize_name(rebate_data.get("item", ""), ctx.name_aliases)
    if not rebate_item:
        return 0.0, ""

    rebate_price = reagent_unit_price(ctx, rebate_item)
    if rebate_price is None or rebate_price <= 0:
        return 0.0, ""

    rebate_yield = rebate_data.get("expected_yield_per_mill")
    herb_overrides = rebate_data.get("expected_yield_per_mill_by_herb", {})
    herb_override = get_named_entry(herb_overrides, herb_name, ctx.name_aliases)
    if herb_override is not None:
        rebate_yield = herb_override[1]

    if rebate_yield is None:
        return 0.0, ""

    rebate_yield = float(rebate_yield)
    if rebate_yield <= 0:
        return 0.0, ""

    rebate_value = rebate_yield * rebate_price
    rebate_detail = (
        f" Includes {format_qty(rebate_yield)} expected {rebate_item} per mill "
        f"for a {copper_to_gold(int(round(rebate_value)))} herb-cost rebate."
    )
    return rebate_value, rebate_detail


def resolve_recipe_output_profile(
    ctx: PricingContext,
    recipe_entry: Dict[str, Any],
) -> RecipeOutputProfile:
    base_output_raw = recipe_entry.get("output_qty", 1)
    try:
        base_output = float(base_output_raw)
    except (TypeError, ValueError):
        base_output = 1.0

    if base_output <= 0:
        return RecipeOutputProfile(
            base_output=0.0,
            expected_output=0.0,
            source="",
        )

    expected_output = base_output
    source = ""
    category_rules = ctx.alchemy_specializations.get("expected_output_multipliers_by_category", {})
    category_rule = category_rules.get(recipe_entry.get("category", ""))

    multiplier = 1.0
    if isinstance(category_rule, dict):
        try:
            multiplier = float(category_rule.get("multiplier", 1.0))
        except (TypeError, ValueError):
            multiplier = 1.0
        source = str(category_rule.get("source", "")).strip()
    elif isinstance(category_rule, (int, float)):
        multiplier = float(category_rule)

    if multiplier > 0:
        expected_output *= multiplier

    return RecipeOutputProfile(
        base_output=base_output,
        expected_output=expected_output,
        source=source,
    )


def make_cost_option(
    item_name: str,
    unit_cost: int,
    source_type: str,
    source_summary: str,
    source_detail: str,
    chain: str,
) -> CostOption:
    return CostOption(
        item=item_name,
        unit_cost=unit_cost,
        source_type=source_type,
        source_summary=source_summary,
        source_detail=source_detail,
        chain=chain,
    )


def make_flat_craft_option(item_name: str, crafted: ReagentResolution) -> CostOption:
    return make_named_flat_craft_option(item_name, crafted)


def make_named_flat_craft_option(
    item_name: str,
    crafted: ReagentResolution,
    method_name: Optional[str] = None,
    note: str = "",
) -> CostOption:
    detail = f"Crafted from {crafted.chain}."
    chain = f"craft({crafted.chain})"

    if method_name:
        detail = f"Crafted via {method_name} from {crafted.chain}."
        chain = f"craft[{method_name}]({crafted.chain})"

    if note:
        detail = f"{detail} {note}".strip()

    return make_cost_option(
        item_name=item_name,
        unit_cost=crafted.total_cost,
        source_type="crafted",
        source_summary="craft",
        source_detail=detail,
        chain=chain,
    )


def resolve_recipe_craft_cost(
    ctx: PricingContext,
    recipe_entry: Dict[str, Any],
    stack: Optional[Set[str]] = None,
) -> Optional[CraftedCost]:
    if not recipe_entry.get("reagents"):
        return None

    crafted = resolve_reagent_list(ctx, recipe_entry["reagents"], stack)
    if crafted is None:
        return None

    output_profile = resolve_recipe_output_profile(ctx, recipe_entry)
    if output_profile.expected_output <= 0:
        return None

    unit_cost = int(round(crafted.total_cost / output_profile.expected_output))
    source_detail = f"Crafted via recipe entry from {crafted.chain}."
    cost_detail = ""
    item_name = normalize_name(recipe_entry.get("item", "crafted item"), ctx.name_aliases)

    if abs(output_profile.expected_output - output_profile.base_output) > 1e-9:
        source = output_profile.source or "specialization"
        source_detail += (
            f" Expected output valued at {format_qty(output_profile.expected_output)} per craft via {source}."
        )
        cost_detail = (
            f"Recipe input cost {copper_to_gold(crafted.total_cost)} per craft. "
            f"Expected output valued at {format_qty(output_profile.expected_output)} {item_name} per craft "
            f"via {source}, for an effective unit cost of {copper_to_gold(unit_cost)}."
        )
    elif abs(output_profile.base_output - 1.0) > 1e-9:
        source_detail += f" Base recipe output {format_qty(output_profile.base_output)} per craft."
        cost_detail = (
            f"Recipe input cost {copper_to_gold(crafted.total_cost)} per craft. "
            f"Base recipe output valued at {format_qty(output_profile.base_output)} {item_name} per craft, "
            f"for an effective unit cost of {copper_to_gold(unit_cost)}."
        )

    return CraftedCost(
        unit_cost=unit_cost,
        components=crafted.components,
        component_chain=crafted.chain,
        chain=f"craft({crafted.chain})",
        source_detail=source_detail,
        cost_detail=cost_detail,
    )


def resolve_reagent_list(
    ctx: PricingContext,
    reagents: List[Dict[str, Any]],
    stack: Optional[Set[str]] = None,
) -> Optional[ReagentResolution]:
    active_stack = stack if stack is not None else set()
    total_cost = 0.0
    chain_parts: List[str] = []
    components: List[ReagentComponent] = []

    for reagent in reagents:
        reagent_name = normalize_name(reagent["item"], ctx.name_aliases)
        qty = float(reagent["qty"])
        resolved = resolve_unit_cost(ctx, reagent_name, active_stack)
        if resolved is None:
            return None

        line_cost = resolved.unit_cost * qty
        total_cost += line_cost
        chain_parts.append(f"{format_qty(qty)}x {reagent_name}->{resolved.chain}")
        components.append(
            ReagentComponent(
                item=reagent_name,
                qty=int(qty) if abs(qty - round(qty)) < 1e-9 else qty,
                unit_cost=resolved.unit_cost,
                total_cost=int(round(line_cost)),
                source_type=resolved.source_type,
                source_summary=resolved.source_summary,
                source_detail=resolved.source_detail,
                source_chain=resolved.chain,
            )
        )

    return ReagentResolution(
        total_cost=int(round(total_cost)),
        chain="; ".join(chain_parts),
        components=components,
    )


def collect_market_option(ctx: PricingContext, item_name: str) -> Optional[CostOption]:
    item = ctx.snapshot.get(item_name)
    if item is None:
        return None
    return make_cost_option(
        item_name=item_name,
        unit_cost=item["price"],
        source_type="market",
        source_summary="AH",
        source_detail="Direct auction house market price.",
        chain="AH",
    )


def collect_recipe_option(
    ctx: PricingContext,
    item_name: str,
    stack: Set[str],
) -> Optional[CostOption]:
    tailoring_entry = get_named_entry(ctx.tailoring_subcrafts, item_name, ctx.name_aliases)
    if tailoring_entry is not None:
        _, tailoring_data = tailoring_entry
        if tailoring_data.get("pricing_mode") == "support_options_only":
            return None

    recipe_entry = ctx.recipe_lookup.get(item_name)
    if recipe_entry is None or not recipe_entry.get("reagents"):
        return None
    crafted = resolve_recipe_craft_cost(ctx, recipe_entry, stack)
    if crafted is None:
        return None
    return make_cost_option(
        item_name=item_name,
        unit_cost=crafted.unit_cost,
        source_type="crafted",
        source_summary="craft",
        source_detail=crafted.source_detail,
        chain=crafted.chain,
    )


def collect_ink_option(
    ctx: PricingContext,
    item_name: str,
    stack: Set[str],
) -> Optional[CostOption]:
    ink_entry = get_named_entry(ctx.inscription.get("inks", {}), item_name, ctx.name_aliases)
    if ink_entry is None or not ink_entry[1].get("crafted_from"):
        return None
    crafted = resolve_reagent_list(ctx, ink_entry[1]["crafted_from"], stack)
    if crafted is None:
        return None
    return make_flat_craft_option(item_name, crafted)


def collect_tailoring_subcraft_option(
    ctx: PricingContext,
    item_name: str,
    stack: Set[str],
) -> Optional[CostOption]:
    tailoring_entry = get_named_entry(ctx.tailoring_subcrafts, item_name, ctx.name_aliases)
    if tailoring_entry is None:
        return None

    entry_data = tailoring_entry[1]
    options: List[Tuple[Optional[str], str, List[Dict[str, Any]]]] = []

    crafted_from = entry_data.get("crafted_from")
    if crafted_from:
        options.append((None, str(entry_data.get("note", "")).strip(), crafted_from))

    for index, option_data in enumerate(entry_data.get("crafted_from_options", []), start=1):
        option_reagents = option_data.get("crafted_from")
        if not option_reagents:
            continue

        option_name = str(
            option_data.get("name")
            or option_data.get("label")
            or f"option {index}"
        ).strip()
        option_note = str(option_data.get("note", "")).strip()
        options.append((option_name, option_note, option_reagents))

    best_option: Optional[CostOption] = None
    for option_name, option_note, option_reagents in options:
        crafted = resolve_reagent_list(ctx, option_reagents, stack)
        if crafted is None:
            continue

        cost_option = make_named_flat_craft_option(
            item_name,
            crafted,
            method_name=option_name,
            note=option_note,
        )
        if best_option is None or cost_option.unit_cost < best_option.unit_cost:
            best_option = cost_option

    return best_option


def collect_vendor_trade_option(
    ctx: PricingContext,
    item_name: str,
    stack: Set[str],
) -> Optional[CostOption]:
    trade_entry = get_named_entry(
        ctx.inscription.get("vendor_trades", {}),
        item_name,
        ctx.name_aliases,
    )
    if trade_entry is None or not trade_entry[1].get("cost"):
        return None
    traded = resolve_reagent_list(ctx, trade_entry[1]["cost"], stack)
    if traded is None:
        return None
    note = trade_entry[1].get("note", "Vendor trade path.")
    return make_cost_option(
        item_name=item_name,
        unit_cost=traded.total_cost,
        source_type="vendor_trade",
        source_summary="trade",
        source_detail=f"{note} Cost path: {traded.chain}.",
        chain=f"trade({traded.chain})",
    )


def resolve_milling_cost(
    ctx: PricingContext,
    item_name: str,
    stack: Optional[Set[str]] = None,
) -> Optional[CostOption]:
    active_stack = stack if stack is not None else set()
    pigment_entry = get_named_entry(ctx.milling.get("pigments", {}), item_name, ctx.name_aliases)
    if pigment_entry is None:
        return None

    pigment_name, pigment_data = pigment_entry
    rules = ctx.milling.get("rules", {})
    herbs_per_mill = float(rules.get("herbs_per_mill", 5))
    quality = pigment_data.get("quality", "common")

    expected_yield = pigment_data.get("expected_pigment_per_mill")
    expected_yield_by_herb = pigment_data.get("expected_pigment_per_mill_by_herb", {})

    best_option: Optional[CostOption] = None
    for herb in pigment_data.get("milled_from", []):
        herb_name = normalize_name(herb, ctx.name_aliases)
        herb_cost = resolve_unit_cost(ctx, herb_name, active_stack)
        if herb_cost is None:
            continue

        herb_expected_yield = expected_yield
        herb_override = get_named_entry(expected_yield_by_herb, herb_name, ctx.name_aliases)
        if herb_override is not None:
            herb_expected_yield = herb_override[1]
        if herb_expected_yield is None:
            if quality == "common":
                herb_expected_yield = rules.get("expected_common_pigment_per_mill")
            else:
                herb_expected_yield = rules.get("expected_uncommon_pigment_per_mill")
        if not herb_expected_yield or herb_expected_yield <= 0:
            continue

        herb_input_cost = herb_cost.unit_cost * herbs_per_mill
        rebate_value, rebate_detail = resolve_milling_rebate_value(ctx, pigment_name, herb_name)
        effective_input_cost = max(herb_input_cost - rebate_value, 0.0)
        unit_cost = int(round(effective_input_cost / float(herb_expected_yield)))
        option = make_cost_option(
            item_name=pigment_name,
            unit_cost=unit_cost,
            source_type="milling",
            source_summary=f"mill {herb_name}",
            source_detail=(
                f"Milling via {format_qty(herbs_per_mill)}x {herb_name} per cast "
                f"with {herb_expected_yield} expected {quality} pigment per mill."
                f"{rebate_detail}"
            ),
            chain=f"mill:{herb_name}",
        )
        if best_option is None or option.unit_cost < best_option.unit_cost:
            best_option = option

    return best_option


def collect_milling_option(
    ctx: PricingContext,
    item_name: str,
    stack: Set[str],
) -> Optional[CostOption]:
    return resolve_milling_cost(ctx, item_name, stack)


def collect_non_ah_option(ctx: PricingContext, item_name: str) -> Optional[CostOption]:
    if item_name not in ctx.non_ah_reagent_prices:
        return None
    return make_cost_option(
        item_name=item_name,
        unit_cost=ctx.non_ah_reagent_prices[item_name],
        source_type="non_ah",
        source_summary="non-AH",
        source_detail="Non-AH reagent excluded from gold-spend ranking.",
        chain="non-AH",
    )


def collect_fallback_option(ctx: PricingContext, item_name: str) -> Optional[CostOption]:
    if item_name not in ctx.fallback_prices:
        return None
    return make_cost_option(
        item_name=item_name,
        unit_cost=ctx.fallback_prices[item_name],
        source_type="fallback",
        source_summary="vendor",
        source_detail="Fixed vendor fallback price.",
        chain="vendor",
    )


def resolve_unit_cost(
    ctx: PricingContext,
    item_name: str,
    stack: Optional[Set[str]] = None,
) -> Optional[CostOption]:
    normalized = normalize_name(item_name, ctx.name_aliases)
    if normalized in ctx.cost_cache:
        return ctx.cost_cache[normalized]

    active_stack = stack if stack is not None else set()
    if normalized in active_stack:
        return None

    active_stack.add(normalized)
    try:
        options = [
            option
            for option in (
                collect_market_option(ctx, normalized),
                collect_recipe_option(ctx, normalized, active_stack),
                collect_ink_option(ctx, normalized, active_stack),
                collect_tailoring_subcraft_option(ctx, normalized, active_stack),
                collect_vendor_trade_option(ctx, normalized, active_stack),
                collect_milling_option(ctx, normalized, active_stack),
                collect_non_ah_option(ctx, normalized),
                collect_fallback_option(ctx, normalized),
            )
            if option is not None
        ]
    finally:
        active_stack.remove(normalized)

    if not options:
        return None

    best_options = options
    if normalized in ctx.force_crafted_cost_items:
        crafted_options = [
            option for option in options
            if option.source_type in {"crafted", "vendor_trade", "milling"}
        ]
        if crafted_options:
            best_options = crafted_options

    best_option = min(best_options, key=lambda option: option.unit_cost)
    ctx.cost_cache[normalized] = best_option
    return best_option


def build_pricing_debug_entry(
    ctx: PricingContext,
    item_name: str,
) -> Dict[str, Any]:
    normalized = normalize_name(item_name, ctx.name_aliases)
    snapshot_entry = ctx.snapshot.get(normalized)
    resolved = resolve_unit_cost(ctx, normalized)

    return {
        "requested_item": item_name,
        "item": normalized,
        "snapshot": snapshot_entry,
        "resolved_cost": resolved.to_dict() if resolved is not None else None,
    }
