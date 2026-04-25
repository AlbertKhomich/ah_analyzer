from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "input"
OUTPUT_DIR = DATA_DIR / "output"
HISTORY_DIR = DATA_DIR / "history"
ORGANIZED_DIR = DATA_DIR / "organized"

AH_SNAPSHOT_CSV = INPUT_DIR / "ah_snapshot.csv"
CRAFTING_JSON = INPUT_DIR / "crafting_data.json"
CLASS_SPEC_ITEMS_JSON = INPUT_DIR / "class_spec_items.json"
ALWAYS_PROFIT_CRAFT_JSON = INPUT_DIR / "always_profit_craft.json"
EVENT_CALENDAR_JSON = INPUT_DIR / "wow_mop_classic_event_dates.json"
