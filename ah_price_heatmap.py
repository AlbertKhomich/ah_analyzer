from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
import pandas as pd
import seaborn as sns


LOGGER = logging.getLogger(__name__)
SNAPSHOT_FILENAME_FORMAT = "%m.%d.%Y_%H.%M.%S_ah_snapshot.csv"
GLOBAL_COLORBAR_LABEL = "Price (gold)"
ROW_COLORBAR_LABEL = "Relative price within item (0=min, 1=max)"
SCROLLABLE_WINDOW_TITLE = "Auction House Prices Over Time"
SCROLLABLE_WINDOW_GEOMETRY = "1400x900"


def parse_snapshot_time(filename: str) -> datetime | None:
    try:
        return datetime.strptime(filename, SNAPSHOT_FILENAME_FORMAT)
    except ValueError:
        return None


def load_ah_snapshots(input_dir: str) -> pd.DataFrame:
    """Load Auction House snapshots into a tidy time-series DataFrame.

    Duplicate item rows within a single snapshot are aggregated with the mean
    price after converting from copper to gold.
    """

    input_path = Path(input_dir)
    if not input_path.exists():
        raise FileNotFoundError(f"Snapshot directory does not exist: {input_dir}")
    if not input_path.is_dir():
        raise NotADirectoryError(f"Snapshot path is not a directory: {input_dir}")

    snapshot_frames: list[pd.DataFrame] = []
    csv_paths = sorted(input_path.glob("*.csv"))
    if not csv_paths:
        raise FileNotFoundError(f"No CSV snapshot files found in: {input_dir}")

    for csv_path in csv_paths:
        snapshot_time = parse_snapshot_time(csv_path.name)
        if snapshot_time is None:
            LOGGER.warning(
                "Skipping file with unparsable snapshot name: %s",
                csv_path.name,
            )
            continue

        try:
            snapshot_frame = pd.read_csv(
                csv_path,
                usecols=["Price", "Name"],
                encoding="utf-8-sig",
            )
        except ValueError as exc:
            LOGGER.warning(
                "Skipping %s because required columns are missing: %s",
                csv_path.name,
                exc,
            )
            continue
        except Exception as exc:  # pragma: no cover - defensive read failure handling
            LOGGER.warning("Skipping %s because it could not be read: %s", csv_path.name, exc)
            continue

        snapshot_frame = snapshot_frame.copy()
        snapshot_frame["Name"] = snapshot_frame["Name"].astype("string").fillna("").str.strip()
        snapshot_frame["Price"] = pd.to_numeric(snapshot_frame["Price"], errors="coerce")

        cleaned_frame = snapshot_frame[
            snapshot_frame["Name"].ne("") & snapshot_frame["Price"].notna()
        ].copy()
        cleaned_frame = cleaned_frame[cleaned_frame["Price"] >= 0]

        dropped_rows = len(snapshot_frame) - len(cleaned_frame)
        if dropped_rows:
            LOGGER.warning(
                "Dropped %d invalid price/name rows from %s",
                dropped_rows,
                csv_path.name,
            )

        if cleaned_frame.empty:
            LOGGER.warning("Skipping %s because no valid rows remained after cleaning", csv_path.name)
            continue

        duplicate_count = int(cleaned_frame.duplicated(subset=["Name"]).sum())
        if duplicate_count:
            LOGGER.info(
                "Aggregating %d duplicate item rows in %s using mean price",
                duplicate_count,
                csv_path.name,
            )

        cleaned_frame["snapshot_time"] = pd.Timestamp(snapshot_time)
        cleaned_frame["price_gold"] = cleaned_frame["Price"].astype(float) / 10000.0
        aggregated_frame = (
            cleaned_frame.groupby(["snapshot_time", "Name"], as_index=False)["price_gold"]
            .mean()
            .loc[:, ["snapshot_time", "Name", "price_gold"]]
        )
        snapshot_frames.append(aggregated_frame)

    if not snapshot_frames:
        raise ValueError(f"No valid Auction House snapshot data could be loaded from: {input_dir}")

    combined_frame = pd.concat(snapshot_frames, ignore_index=True)
    combined_frame = (
        combined_frame.groupby(["snapshot_time", "Name"], as_index=False)["price_gold"]
        .mean()
        .sort_values(["snapshot_time", "Name"], ascending=[True, True], ignore_index=True)
    )
    return combined_frame


def _row_normalize(matrix: pd.DataFrame) -> pd.DataFrame:
    def normalize_row(row: pd.Series) -> pd.Series:
        valid_values = row.dropna()
        if valid_values.empty:
            return row

        row_min = float(valid_values.min())
        row_max = float(valid_values.max())
        if row_max == row_min:
            normalized_row = pd.Series(0.5, index=row.index, dtype=float)
            normalized_row[row.isna()] = float("nan")
            return normalized_row

        return (row - row_min) / (row_max - row_min)

    return matrix.apply(normalize_row, axis=1)


def _build_annotation_matrix(matrix: pd.DataFrame) -> pd.DataFrame:
    return matrix.apply(
        lambda column: column.map(
            lambda value: "" if pd.isna(value) else f"{float(value):.2f}"
        )
    )


def _format_snapshot_labels(timestamps: pd.Index) -> list[str]:
    labels: list[str] = []
    for timestamp in timestamps:
        labels.append(pd.Timestamp(timestamp).strftime("%Y-%m-%d\n%H:%M:%S"))
    return labels


def _resolve_figure_size(
    item_count: int,
    snapshot_count: int,
    annotate: bool,
    scrollable: bool = False,
) -> tuple[float, float]:
    column_width = 0.85 if annotate else 0.55
    row_height = 0.24 if annotate else 0.18
    max_width = 42.0 if scrollable else 24.0
    max_height = 72.0 if scrollable else 32.0
    min_width = 10.0 if scrollable else 8.0
    min_height = 8.0 if scrollable else 6.0
    width = max(min_width, min(max_width, 4.0 + (snapshot_count * column_width)))
    height = max(min_height, min(max_height, 2.5 + (item_count * row_height)))
    return width, height


def _mousewheel_units(event: object) -> int:
    button_number = getattr(event, "num", None)
    if button_number == 4:
        return -3
    if button_number == 5:
        return 3

    delta = int(getattr(event, "delta", 0))
    if delta == 0:
        return 0

    direction = -1 if delta > 0 else 1
    magnitude = max(1, int(round(abs(delta) / 120)))
    return direction * magnitude * 3


def _highlight_row_extrema(ax: plt.Axes, matrix: pd.DataFrame) -> None:
    saw_equal_extrema = False
    min_color = "#1f77b4"
    max_color = "#d62728"
    equal_color = "#6f42c1"

    for row_index, (_, row) in enumerate(matrix.iterrows()):
        valid_values = row.dropna()
        if valid_values.empty:
            continue

        min_column = row.idxmin()
        max_column = row.idxmax()
        min_column_index = matrix.columns.get_loc(min_column)
        max_column_index = matrix.columns.get_loc(max_column)

        if min_column == max_column:
            saw_equal_extrema = True
            ax.add_patch(
                Rectangle(
                    (min_column_index, row_index),
                    1,
                    1,
                    fill=False,
                    edgecolor=equal_color,
                    linewidth=2.8,
                )
            )
            continue

        ax.add_patch(
            Rectangle(
                (min_column_index, row_index),
                1,
                1,
                fill=False,
                edgecolor=min_color,
                linewidth=2.4,
            )
        )
        ax.add_patch(
            Rectangle(
                (max_column_index, row_index),
                1,
                1,
                fill=False,
                edgecolor=max_color,
                linewidth=2.4,
                linestyle="--",
            )
        )

    legend_handles = [
        Rectangle(
            (0, 0),
            1,
            1,
            fill=False,
            edgecolor=min_color,
            linewidth=2.4,
            label="Lowest price",
        ),
        Rectangle(
            (0, 0),
            1,
            1,
            fill=False,
            edgecolor=max_color,
            linewidth=2.4,
            linestyle="--",
            label="Highest price",
        ),
    ]
    if saw_equal_extrema:
        legend_handles.append(
            Rectangle(
                (0, 0),
                1,
                1,
                fill=False,
                edgecolor=equal_color,
                linewidth=2.8,
                label="Only observed price",
            )
        )

    ax.legend(
        handles=legend_handles,
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        frameon=False,
        borderaxespad=0.0,
    )


def _render_scrollable_figure(fig: Figure, title: str) -> None:
    try:
        import tkinter as tk
        from tkinter import ttk

        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    except Exception as exc:  # pragma: no cover - depends on local GUI support
        LOGGER.warning(
            "Scrollable viewer unavailable (%s). Falling back to matplotlib show().",
            exc,
        )
        plt.show()
        return

    try:
        root = tk.Tk()
    except Exception as exc:  # pragma: no cover - depends on local display support
        LOGGER.warning(
            "Could not open scrollable viewer (%s). Falling back to matplotlib show().",
            exc,
        )
        plt.show()
        return

    root.title(title)
    root.geometry(SCROLLABLE_WINDOW_GEOMETRY)
    root.minsize(900, 600)

    container = ttk.Frame(root, padding=(8, 8, 8, 8))
    container.pack(fill="both", expand=True)
    container.grid_columnconfigure(0, weight=1)
    container.grid_rowconfigure(2, weight=1)

    toolbar_frame = ttk.Frame(container)
    toolbar_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))

    hint_label = ttk.Label(
        container,
        text="Scroll vertically with the mouse wheel. Hold Shift to scroll horizontally.",
    )
    hint_label.grid(row=1, column=0, sticky="w", pady=(0, 6))

    scroll_canvas = tk.Canvas(container, highlightthickness=0)
    scroll_canvas.grid(row=2, column=0, sticky="nsew")

    vertical_scrollbar = ttk.Scrollbar(
        container,
        orient="vertical",
        command=scroll_canvas.yview,
    )
    vertical_scrollbar.grid(row=2, column=1, sticky="ns")

    horizontal_scrollbar = ttk.Scrollbar(
        container,
        orient="horizontal",
        command=scroll_canvas.xview,
    )
    horizontal_scrollbar.grid(row=3, column=0, sticky="ew", pady=(6, 0))

    scroll_canvas.configure(
        xscrollcommand=horizontal_scrollbar.set,
        yscrollcommand=vertical_scrollbar.set,
    )

    figure_frame = ttk.Frame(scroll_canvas)
    scroll_canvas.create_window((0, 0), window=figure_frame, anchor="nw")

    figure_canvas = FigureCanvasTkAgg(fig, master=figure_frame)
    toolbar = NavigationToolbar2Tk(figure_canvas, toolbar_frame, pack_toolbar=False)
    toolbar.update()
    toolbar.pack(anchor="w")

    figure_widget = figure_canvas.get_tk_widget()
    figure_widget.pack()
    figure_canvas.draw()

    def refresh_scroll_region(_event: object | None = None) -> None:
        scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))

    def on_vertical_scroll(event: object) -> str:
        units = _mousewheel_units(event)
        if units:
            scroll_canvas.yview_scroll(units, "units")
        return "break"

    def on_horizontal_scroll(event: object) -> str:
        units = _mousewheel_units(event)
        if units:
            scroll_canvas.xview_scroll(units, "units")
        return "break"

    for sequence, callback in [
        ("<MouseWheel>", on_vertical_scroll),
        ("<Button-4>", on_vertical_scroll),
        ("<Button-5>", on_vertical_scroll),
        ("<Shift-MouseWheel>", on_horizontal_scroll),
        ("<Shift-Button-4>", on_horizontal_scroll),
        ("<Shift-Button-5>", on_horizontal_scroll),
    ]:
        root.bind_all(sequence, callback)

    figure_frame.bind("<Configure>", refresh_scroll_region)
    refresh_scroll_region()

    def on_close() -> None:
        for sequence in [
            "<MouseWheel>",
            "<Button-4>",
            "<Button-5>",
            "<Shift-MouseWheel>",
            "<Shift-Button-4>",
            "<Shift-Button-5>",
        ]:
            root.unbind_all(sequence)
        plt.close(fig)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def _build_price_heatmap_figure(
    input_dir: str,
    output_path: str | None = None,
    normalize: str = "global",
    annotate: bool = True,
    scrollable: bool = False,
) -> Figure:
    if output_path is not None:
        plt.switch_backend("Agg")

    if normalize not in {"global", "row"}:
        raise ValueError("normalize must be either 'global' or 'row'")

    snapshot_data = load_ah_snapshots(input_dir)
    price_matrix = snapshot_data.pivot_table(
        index="Name",
        columns="snapshot_time",
        values="price_gold",
        aggfunc="mean",
    )
    price_matrix = price_matrix.sort_index(axis=0).sort_index(axis=1)

    if price_matrix.empty:
        raise ValueError(f"No heatmap data available after loading snapshots from: {input_dir}")

    if normalize == "row":
        heatmap_values = _row_normalize(price_matrix)
        colorbar_label = ROW_COLORBAR_LABEL
        vmin, vmax = 0.0, 1.0
    else:
        heatmap_values = price_matrix
        valid_values = price_matrix.stack().dropna()
        if valid_values.empty:
            raise ValueError(f"No valid price values available in: {input_dir}")

        vmin = float(valid_values.min())
        vmax = float(valid_values.max())
        if vmin == vmax:
            vmax = vmin + 1e-9
        colorbar_label = GLOBAL_COLORBAR_LABEL

    annotation_matrix = _build_annotation_matrix(price_matrix) if annotate else None
    figure_width, figure_height = _resolve_figure_size(
        item_count=len(price_matrix.index),
        snapshot_count=len(price_matrix.columns),
        annotate=annotate,
        scrollable=scrollable,
    )
    annotation_fontsize = max(4, min(8, 10 - (len(price_matrix.index) // 25)))

    fig, ax = plt.subplots(figsize=(figure_width, figure_height))
    sns.heatmap(
        heatmap_values,
        ax=ax,
        cmap="YlGnBu",
        mask=price_matrix.isna(),
        linewidths=0.5,
        linecolor="white",
        annot=annotation_matrix if annotate else False,
        fmt="" if annotate else ".2f",
        annot_kws={"fontsize": annotation_fontsize},
        cbar_kws={"label": colorbar_label},
        vmin=vmin,
        vmax=vmax,
    )

    ax.set_title("Auction House Prices Over Time")
    ax.set_xlabel("Snapshot time")
    ax.set_ylabel("Item name")
    ax.set_xticklabels(_format_snapshot_labels(price_matrix.columns), rotation=45, ha="right")
    ax.set_yticklabels(ax.get_yticklabels(), rotation=0)

    _highlight_row_extrema(ax, price_matrix)
    fig.tight_layout()

    if output_path:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_file, dpi=150, bbox_inches="tight")
        LOGGER.info("Saved heatmap to %s", output_file)

    return fig


def plot_price_heatmap(
    input_dir: str,
    output_path: str | None = None,
    normalize: str = "global",
    annotate: bool = True,
) -> Figure:
    return _build_price_heatmap_figure(
        input_dir=input_dir,
        output_path=output_path,
        normalize=normalize,
        annotate=annotate,
        scrollable=False,
    )


def show_scrollable_heatmap(
    input_dir: str,
    normalize: str = "global",
    annotate: bool = True,
) -> Figure:
    fig = _build_price_heatmap_figure(
        input_dir=input_dir,
        output_path=None,
        normalize=normalize,
        annotate=annotate,
        scrollable=True,
    )
    _render_scrollable_figure(fig, title=SCROLLABLE_WINDOW_TITLE)
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot Auction House item prices over time as a heatmap.",
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="history",
        help="Directory containing snapshot CSV files.",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_path",
        help="Optional path to save the rendered heatmap image.",
    )
    parser.add_argument(
        "--normalize",
        choices=["global", "row"],
        default="global",
        help="Color normalization mode for the heatmap.",
    )
    parser.add_argument(
        "--no-annotate",
        action="store_true",
        help="Disable cell text annotations.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    fig = plot_price_heatmap(
        input_dir=args.input_dir,
        output_path=args.output_path,
        normalize=args.normalize,
        annotate=not args.no_annotate,
    ) if args.output_path is not None else show_scrollable_heatmap(
        input_dir=args.input_dir,
        normalize=args.normalize,
        annotate=not args.no_annotate,
    )

    if args.output_path is not None:
        plt.close(fig)


if __name__ == "__main__":
    main()
