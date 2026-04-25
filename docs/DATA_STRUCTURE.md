# Data Structure

The repository is organized by role:

- `scripts/`: commands you run directly.
- `src/ah_trading/`: reusable Python modules used by the scripts.
- `data/input/`: source data such as the live AH snapshot and recipe JSON.
- `data/history/`: archived Auction House snapshots.
- `data/output/`: generated plans, reports, images, and shopping lists.

Common commands:

```powershell
.\.venv\Scripts\python.exe scripts\think.py
.\.venv\Scripts\python.exe scripts\pricing_debug.py --item "Imperial Silk"
```
