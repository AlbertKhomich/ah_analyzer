from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ah_trading.paths import (
    ALWAYS_PROFIT_CRAFT_JSON,
    CLASS_SPEC_ITEMS_JSON,
    EVENT_CALENDAR_JSON,
)


PLANNER_JSON_FILES = [
    str(CLASS_SPEC_ITEMS_JSON),
    str(ALWAYS_PROFIT_CRAFT_JSON),
]
EVENT_CALENDAR_JSON_PATH = str(EVENT_CALENDAR_JSON)


def load_json(json_path: str) -> Dict[str, Any]:
    with open(json_path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_planner_data(json_paths: List[str]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {
        "meta": {"sources": [], "notes": []},
        "item_index": {},
        "shared_item_groups": {},
        "classes": {},
    }

    loaded_any = False
    for json_path in json_paths:
        path = Path(json_path)
        if not path.exists():
            continue

        loaded_any = True
        data = load_json(str(path))
        merged["meta"]["sources"].append(path.name)

        for note in data.get("meta", {}).get("notes", []):
            if note not in merged["meta"]["notes"]:
                merged["meta"]["notes"].append(note)

        for item_name, item_data in data.get("item_index", {}).items():
            merged["item_index"][item_name] = item_data

        for group_name, group_data in data.get("shared_item_groups", {}).items():
            merged["shared_item_groups"][group_name] = group_data

        for class_name, class_block in data.get("classes", {}).items():
            target_class = merged["classes"].setdefault(class_name, {})
            for spec_name, spec_block in class_block.items():
                target_class[spec_name] = spec_block

    if not loaded_any:
        raise FileNotFoundError(
            f"No planner data files found. Checked: {', '.join(json_paths)}"
        )

    return merged


def parse_iso_date(value: Any) -> Optional[date]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def get_active_event_slugs(
    event_calendar_path: str = EVENT_CALENDAR_JSON_PATH,
    on_date: Optional[date] = None,
) -> Set[str]:
    path = Path(event_calendar_path)
    if not path.exists():
        return set()

    calendar_data = load_json(str(path))
    target_date = on_date or date.today()
    active_slugs: Set[str] = set()

    for event in calendar_data.get("events", []):
        slug = str(event.get("slug", "")).strip()
        if not slug:
            continue

        for key, window in event.items():
            if not key.startswith("dates_") or not isinstance(window, dict):
                continue

            start_date = parse_iso_date(window.get("start_date"))
            end_date = parse_iso_date(window.get("end_date"))
            if start_date is None or end_date is None:
                continue

            if start_date <= target_date <= end_date:
                active_slugs.add(slug)
                break

    return active_slugs


def merge_active_event_entries(
    planner_data: Dict[str, Any],
    crafting_data: Dict[str, Any],
    event_calendar_path: str = EVENT_CALENDAR_JSON_PATH,
    on_date: Optional[date] = None,
) -> Dict[str, Any]:
    merged = deepcopy(planner_data)
    target_date = on_date or date.today()
    active_slugs = get_active_event_slugs(
        event_calendar_path=event_calendar_path,
        on_date=target_date,
    )

    if not active_slugs:
        return merged

    added_items: List[str] = []
    item_index = merged.setdefault("item_index", {})
    shared_groups = merged.setdefault("shared_item_groups", {})
    meta = merged.setdefault("meta", {})
    meta_notes = meta.setdefault("notes", [])

    for entry in crafting_data.get("craft_targets", []):
        entry_event_slugs = {
            str(slug).strip()
            for slug in entry.get("events", [])
            if str(slug).strip()
        }
        if not entry_event_slugs or not (entry_event_slugs & active_slugs):
            continue

        item_name = str(entry.get("item", "")).strip()
        if not item_name:
            continue

        if item_name not in item_index:
            item_index[item_name] = {
                "rank": int(entry.get("rank", 0)),
                "category": entry.get("category", ""),
                "tier": entry.get("tier", "C"),
                "reason": entry.get("reason", ""),
            }

        added_items.append(item_name)

    if not added_items:
        return merged

    shared_groups["active_event_crafts"] = {
        "note": (
            f"Crafts active on {target_date.isoformat()} from the current MoP Classic "
            f"event calendar."
        ),
        "items": added_items,
    }

    note = (
        f"Active event crafts added for {target_date.isoformat()}: "
        f"{', '.join(added_items)}."
    )
    if note not in meta_notes:
        meta_notes.append(note)

    return merged
