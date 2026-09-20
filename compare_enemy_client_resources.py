#!/usr/bin/env python3
"""Show a UI comparing enemy templates against 2.5 and 8.0 clients."""

from __future__ import annotations

import argparse
import subprocess
import struct
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


ROOT = Path(r"C:\Work\SA")
DEFAULT_ENEMYBASE = ROOT / "sa-server" / "2.5" / "gmsv" / "data" / "enemybase.txt"
DEFAULT_25_INDEX = ROOT / "SA2.5" / "stoneage2.5" / "data" / "spradrn_5.bin"
DEFAULT_80_INDEX = ROOT / "SA8.0" / "data" / "spradrn_115.bin"

# Confirmed from the server's enemybase format: column 6 is the template ID;
# column 36 is the client base image/animation number.
TEMPLATE_ID_COLUMN = 6
IMAGE_NUMBER_COLUMN = 36
HEADERS = (
    "enemybase_line",
    "template_name",
    "template_id",
    "image_number",
    "in_client_2.5",
    "in_client_8.0",
)


def sprite_ids(path: Path) -> set[int]:
    data = path.read_bytes()
    if len(data) % 12:
        raise ValueError(f"Unexpected index size: {path}")
    return {image_number for image_number, _offset, _flags in struct.iter_unpack("<III", data)}


def enemybase_rows(path: Path) -> list[tuple[int, str, str, int]]:
    rows: list[tuple[int, str, str, int]] = []
    for line_number, line in enumerate(path.read_text(encoding="gbk", errors="replace").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [value.strip() for value in line.split(",")]
        if len(fields) <= IMAGE_NUMBER_COLUMN:
            continue
        try:
            image_number = int(fields[IMAGE_NUMBER_COLUMN])
        except ValueError:
            continue
        rows.append((line_number, fields[0], fields[TEMPLATE_ID_COLUMN], image_number))
    return rows


def comparison_rows(
    enemybase: Path, client25_index: Path, client80_index: Path
) -> list[tuple[int, str, str, int, str, str]]:
    ids_25 = sprite_ids(client25_index)
    ids_80 = sprite_ids(client80_index)
    rows = []
    for line, name, template_id, image_number in enemybase_rows(enemybase):
        rows.append(
            (
                line,
                name,
                template_id,
                image_number,
                "yes" if image_number in ids_25 else "no",
                "yes" if image_number in ids_80 else "no",
            )
        )
    return rows


class ComparisonWindow:
    def __init__(
        self,
        root: tk.Tk,
        enemybase: Path,
        client25_index: Path,
        client80_index: Path,
    ):
        self.root = root
        root.title("Enemy Client Resource Comparison")
        root.geometry("1100x720")
        root.minsize(760, 420)

        self.paths = {
            "enemybase": tk.StringVar(value=str(enemybase)),
            "client25": tk.StringVar(value=str(client25_index)),
            "client80": tk.StringVar(value=str(client80_index)),
        }
        controls = ttk.Frame(root, padding=10)
        controls.pack(fill="x")
        self._path_row(controls, 0, "Enemy template file", "enemybase", [("Text files", "*.txt"), ("All files", "*.*")])
        self._path_row(controls, 1, "2.5 client index", "client25", [("Binary files", "*.bin"), ("All files", "*.*")])
        self._path_row(controls, 2, "8.0 client index", "client80", [("Binary files", "*.bin"), ("All files", "*.*")])
        controls.columnconfigure(1, weight=1)
        ttk.Button(controls, text="Compare", command=self.load).grid(row=3, column=2, pady=(8, 0), sticky="e")

        filters = ttk.Frame(root, padding=(10, 0, 10, 8))
        filters.pack(fill="x")
        ttk.Label(filters, text="Search").pack(side="left")
        self.search_var = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.search_var, width=36)
        search.pack(side="left", padx=(6, 16))
        search.bind("<KeyRelease>", lambda _event: self._apply_filters())
        ttk.Label(filters, text="Filter").pack(side="left")
        self.filter_var = tk.StringVar(value="All")
        status_filter = ttk.Combobox(
            filters,
            textvariable=self.filter_var,
            values=("All", "Missing from 2.5", "Present in 8.0", "Importable from 8.0"),
            state="readonly",
            width=22,
        )
        status_filter.pack(side="left", padx=6)
        status_filter.bind("<<ComboboxSelected>>", lambda _event: self._apply_filters())
        ttk.Button(filters, text="Clear", command=self._clear_filters).pack(side="left", padx=(4, 0))

        self.summary = tk.StringVar(value="Select the files and click Compare.")
        ttk.Label(root, textvariable=self.summary, padding=(10, 0, 10, 10)).pack(fill="x")

        frame = ttk.Frame(root, padding=(10, 0, 10, 10))
        frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(frame, columns=HEADERS, show="headings", selectmode="none")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        widths = (120, 240, 110, 120, 120, 120)
        for header, width in zip(HEADERS, widths):
            self.table.heading(header, text=header, anchor="w")
            self.table.column(header, width=width, minwidth=80, anchor="w")
        self.table.tag_configure("missing", background="#fff0f0")
        self.table.tag_configure("importable", background="#fff7d6")
        self.active_cell: tuple[str, int] | None = None
        self.all_rows: list[tuple[int, str, str, int, str, str]] = []
        self.last_click = None
        self.table.bind("<Button-1>", self._select_cell)
        self.table.bind("<Double-1>", self._select_cell)
        self.table.bind("<Control-c>", self._copy_cell)
        self.table.bind("<Control-C>", self._copy_cell)
        self.table.bind("<Button-3>", self._show_context_menu)
        self.context_menu = tk.Menu(root, tearoff=False)
        self.context_menu.add_command(label="Copy cell", command=self._copy_cell)

        self.cell_highlight = tk.Label(
            self.table,
            borderwidth=2,
            relief="solid",
            background="#dbeafe",
            foreground="black",
            anchor="w",
            padx=2,
            takefocus=True,
        )
        self.cell_highlight.bind("<Control-c>", self._copy_cell)
        self.cell_highlight.bind("<Control-C>", self._copy_cell)
        self.cell_highlight.bind("<Button-1>", self._select_cell)
        self.cell_highlight.bind("<Double-1>", self._select_cell)
        self.cell_highlight.bind("<Button-3>", self._show_active_context_menu)
        for widget in (self.table, self.cell_highlight):
            for key, delta in {"Left": (0, -1), "Right": (0, 1), "Up": (-1, 0), "Down": (1, 0), "Tab": (0, 1)}.items():
                widget.bind(f"<{key}>", lambda event, delta=delta: self._move_cell(*delta))
        self.table.configure(
            yscrollcommand=lambda *args: self._view_changed(vertical, *args),
            xscrollcommand=lambda *args: self._view_changed(horizontal, *args),
        )
        self.table.bind("<Configure>", lambda event: self.root.after_idle(self._position_cell))

        self.table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(root, text="Click a cell and press Ctrl+C to copy its value.", padding=(10, 0, 10, 8)).pack(fill="x")
        self.load()

    def _path_row(self, parent: ttk.Frame, row: int, label: str, key: str, filetypes: list[tuple[str, str]]) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, padx=(0, 8), pady=3, sticky="w")
        ttk.Entry(parent, textvariable=self.paths[key]).grid(row=row, column=1, pady=3, sticky="ew")
        ttk.Button(
            parent,
            text="Browse...",
            command=lambda: self._browse(key, filetypes),
        ).grid(row=row, column=2, padx=(8, 0), pady=3)

    def _browse(self, key: str, filetypes: list[tuple[str, str]]) -> None:
        current = Path(self.paths[key].get())
        selected = filedialog.askopenfilename(
            parent=self.root,
            initialdir=current.parent if current.parent.exists() else None,
            filetypes=filetypes,
        )
        if selected:
            self.paths[key].set(selected)

    def _show_context_menu(self, event: tk.Event) -> None:
        item = self.table.identify_row(event.y)
        column = self.table.identify_column(event.x)
        if item and column:
            self._activate_cell(item, int(column[1:]) - 1)
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def _show_active_context_menu(self, event: tk.Event) -> None:
        if self.active_cell is not None:
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def _select_cell(self, event: tk.Event) -> str | None:
        x = event.x_root - self.table.winfo_rootx()
        y = event.y_root - self.table.winfo_rooty()
        item = self.table.identify_row(y)
        column = self.table.identify_column(x)
        if item and column:
            cell = (item, int(column[1:]) - 1)
            previous = self.last_click
            self._activate_cell(*cell)
            self.last_click = (cell, event.time)
            if previous and previous[0] == cell and 0 <= event.time - previous[1] <= 500:
                self.last_click = None
                self._open_cell_resource(*cell)
            return "break"

    def _move_cell(self, row_delta: int, column_delta: int) -> str:
        items = self.table.get_children()
        if not items:
            return "break"
        item, column = self.active_cell or (items[0], 0)
        row = max(0, min(len(items) - 1, items.index(item) + row_delta))
        column = max(0, min(len(HEADERS) - 1, column + column_delta))
        self.table.see(items[row])
        self._activate_cell(items[row], column)
        return "break"

    def _view_changed(self, scrollbar: ttk.Scrollbar, *args: str) -> None:
        scrollbar.set(*args)
        self._position_cell()

    def _position_cell(self) -> None:
        if self.active_cell is None:
            self.cell_highlight.place_forget()
            return
        item, column_index = self.active_cell
        bounds = self.table.bbox(item, f"#{column_index + 1}")
        if not bounds:
            self.cell_highlight.place_forget()
            return
        x, y, width, height = bounds
        self.cell_highlight.place(x=x, y=y, width=width, height=height)

    def _activate_cell(self, item: str, column_index: int) -> None:
        values = self.table.item(item, "values")
        if not 0 <= column_index < len(values):
            return
        self.active_cell = (item, column_index)
        selected_rows = self.table.selection()
        if selected_rows:
            self.table.selection_remove(*selected_rows)
        self.table.focus(item)
        self.cell_highlight.configure(text=str(values[column_index]))
        self._position_cell()
        self.table.focus_set()

    def _copy_cell(self, _event: tk.Event | None = None) -> str:
        if self.active_cell is None:
            return "break"
        item, column_index = self.active_cell
        values = self.table.item(item, "values")
        if column_index >= len(values):
            return "break"
        self.root.clipboard_clear()
        self.root.clipboard_append(str(values[column_index]))
        self.root.update_idletasks()
        return "break"

    def _clear_filters(self) -> None:
        self.search_var.set("")
        self.filter_var.set("All")
        self._apply_filters()

    def _apply_filters(self) -> None:
        query = self.search_var.get().strip().casefold()
        selected_filter = self.filter_var.get()

        def matches(row: tuple[int, str, str, int, str, str]) -> bool:
            if query and not any(query in str(value).casefold() for value in row):
                return False
            if selected_filter == "Missing from 2.5":
                return row[4] == "no"
            if selected_filter == "Present in 8.0":
                return row[5] == "yes"
            if selected_filter == "Importable from 8.0":
                return row[4] == "no" and row[5] == "yes"
            return True

        rows = [row for row in self.all_rows if matches(row)]
        self.cell_highlight.place_forget()
        self.active_cell = None
        self.last_click = None
        self.table.delete(*self.table.get_children())
        for row in rows:
            tag = "importable" if row[4] == "no" and row[5] == "yes" else "missing" if row[4] == "no" else ""
            self.table.insert("", "end", values=row, tags=(tag,) if tag else ())

        missing_25 = sum(row[4] == "no" for row in self.all_rows)
        importable = sum(row[4] == "no" and row[5] == "yes" for row in self.all_rows)
        self.summary.set(
            f"Shown: {len(rows)} / {len(self.all_rows)}    Missing from 2.5 client: {missing_25}    "
            f"Available in 8.0 and importable: {importable}"
        )

    def _open_resource(self, event: tk.Event) -> str | None:
        item = self.table.identify_row(event.y)
        column = self.table.identify_column(event.x)
        if not item or not column:
            return None
        column_index = int(column[1:]) - 1
        self._activate_cell(item, column_index)
        return self._open_cell_resource(item, column_index)

    def _open_active_resource(self, _event: tk.Event | None = None) -> str | None:
        if self.active_cell is None:
            return None
        return self._open_cell_resource(*self.active_cell)

    def _open_cell_resource(self, item: str, column_index: int) -> str | None:
        if column_index not in (4, 5):
            return None
        values = self.table.item(item, "values")
        if len(values) <= column_index or values[column_index] != "yes":
            return "break"
        index_key = "client25" if column_index == 4 else "client80"
        data_dir = Path(self.paths[index_key].get()).parent
        viewer = Path(__file__).with_name("sa_resource_viewer.py")
        try:
            subprocess.Popen(
                [sys.executable, str(viewer), "--data-dir", str(data_dir), "--sprite", str(values[3])]
            )
        except OSError as error:
            messagebox.showerror("Unable to open resource viewer", str(error), parent=self.root)
        return "break"

    def load(self) -> None:
        try:
            rows = comparison_rows(
                Path(self.paths["enemybase"].get()),
                Path(self.paths["client25"].get()),
                Path(self.paths["client80"].get()),
            )
        except (OSError, ValueError) as error:
            messagebox.showerror("Unable to compare resources", str(error), parent=self.root)
            return

        self.all_rows = rows
        self._apply_filters()


def main() -> None:
    parser = argparse.ArgumentParser(description="Find server enemy graphics missing from the 2.5 client.")
    parser.add_argument("--enemybase", type=Path, default=DEFAULT_ENEMYBASE)
    parser.add_argument("--client25-index", type=Path, default=DEFAULT_25_INDEX)
    parser.add_argument("--client80-index", type=Path, default=DEFAULT_80_INDEX)
    args = parser.parse_args()
    root = tk.Tk()
    ComparisonWindow(root, args.enemybase, args.client25_index, args.client80_index)
    root.mainloop()


if __name__ == "__main__":
    main()
