#!/usr/bin/env python3

import argparse
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from ah_trading.paths import AH_SNAPSHOT_CSV, HISTORY_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install a new AH snapshot and copy that refreshed snapshot "
            "into data/history/."
        )
    )
    parser.add_argument(
        "new_snapshot",
        help="Path to the new AH snapshot CSV to install as data/input/ah_snapshot.csv.",
    )
    parser.add_argument(
        "--snapshot-path",
        default=str(AH_SNAPSHOT_CSV),
        help="Path to the live AH snapshot file to refresh.",
    )
    parser.add_argument(
        "--history-dir",
        default=str(HISTORY_DIR),
        help="Directory where archived snapshots should be stored.",
    )
    parser.add_argument(
        "--archive-date",
        default=datetime.now().strftime("%m.%d.%Y_%H.%M.%S"),
        help=(
            "Timestamp prefix for the history snapshot copy, formatted like "
            "MM.DD.YYYY_HH.MM.SS."
        ),
    )
    parser.add_argument(
        "--overwrite-history",
        action="store_true",
        help="Overwrite the history file if one already exists for the archive date.",
    )
    return parser.parse_args()


def copy_into_place(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)

    temp_fd, temp_name = tempfile.mkstemp(
        dir=str(target_path.parent),
        prefix=f".{target_path.stem}_",
        suffix=target_path.suffix,
    )
    os.close(temp_fd)
    temp_path = Path(temp_name)

    try:
        shutil.copy2(source_path, temp_path)
        os.replace(temp_path, target_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def main() -> int:
    args = parse_args()

    source_path = Path(args.new_snapshot).expanduser().resolve()
    snapshot_path = Path(args.snapshot_path).expanduser().resolve()
    history_dir = Path(args.history_dir).expanduser().resolve()

    if not source_path.is_file():
        raise FileNotFoundError(f"New snapshot not found: {source_path}")

    if source_path == snapshot_path:
        raise ValueError("New snapshot path matches the live ah_snapshot.csv path.")

    history_path = history_dir / f"{args.archive_date}_ah_snapshot.csv"
    if snapshot_path.exists() and not snapshot_path.is_file():
        raise ValueError(f"Live snapshot is not a file: {snapshot_path}")

    history_dir.mkdir(parents=True, exist_ok=True)
    if history_path.exists() and not args.overwrite_history:
        raise FileExistsError(
            "History file already exists. Re-run with --overwrite-history "
            f"to replace it: {history_path}"
        )

    copy_into_place(source_path, snapshot_path)
    copy_into_place(snapshot_path, history_path)

    print(f"Installed new snapshot from {source_path} to {snapshot_path}")
    print(f"Copied refreshed snapshot to {history_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
