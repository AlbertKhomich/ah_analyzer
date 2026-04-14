import json
from pathlib import Path
from typing import Any, Dict, List


PLANNER_JSON_FILES = [
    "class_spec_items.json",
    "always_profit_craft.json",
]


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
