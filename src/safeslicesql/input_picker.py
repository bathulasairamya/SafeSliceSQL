"""Database discovery and native input dialogs for the presentation menu."""

from pathlib import Path

import sqlalchemy as sa

from safeslicesql.config import AppConfig


def available_databases(config: AppConfig) -> list[str]:
    engine = sa.create_engine(config.source_db.url.set(database=None))
    try:
        names = sa.inspect(engine).get_schema_names()
        system = {"information_schema", "mysql", "performance_schema", "sys"}
        return sorted((name for name in names if name.casefold() not in system), key=str.casefold)
    finally:
        engine.dispose()


def pick_input(kind: str) -> Path | None:
    """Return None on cancellation; no database changes happen in this dialog."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        if kind == "folder":
            selected = filedialog.askdirectory(
                parent=root, title="Choose a folder of CSV files", mustexist=True
            )
        else:
            filetypes = (
                [("CSV files", "*.csv")] if kind == "csv" else [("Excel workbooks", "*.xlsx")]
            )
            selected = filedialog.askopenfilename(
                parent=root, title="Choose the faculty dataset", filetypes=filetypes
            )
        return Path(selected) if selected else None
    finally:
        root.destroy()
