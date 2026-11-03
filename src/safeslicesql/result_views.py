"""Inspectable records and chart windows for the desktop demo."""

import json
import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


def chart_files(folder):
    return sorted((Path(folder) / "plots").glob("*.png"))


def show_charts(root, folder):
    files = chart_files(folder)
    if not files:
        messagebox.showinfo(
            "No charts available",
            "This run has no generated charts. Rejected runs are excluded from benchmark charts. "
            "Check Run details and reports for failed checks or plotting errors.",
            parent=root,
        )
        return
    dialog = tk.Toplevel(root)
    dialog.title("Charts for this copy")
    dialog.geometry("900x680")
    ttk.Label(dialog, text="Choose a chart", padding=10).pack(anchor="w")
    choice = ttk.Combobox(
        dialog, state="readonly", values=[p.stem.replace("_", " ").title() for p in files]
    )
    choice.pack(fill="x", padx=12)
    ttk.Button(dialog, text="Close", command=dialog.destroy).pack(side="bottom", pady=8)
    ttk.Label(
        dialog,
        text="Charts summarize measured runs. Use Verify my copy to inspect actual records.",
        wraplength=820,
    ).pack(side="bottom", pady=8)
    canvas = tk.Canvas(dialog, background="white", highlightthickness=0)
    canvas.pack(fill="both", expand=True, padx=12, pady=12)
    images = {}

    def render(event=None):
        try:
            index = choice.current()
            if index not in images:
                images[index] = tk.PhotoImage(file=str(files[index]))
            original = images[index]
            factor = max(
                1,
                math.ceil(original.width() / max(1, canvas.winfo_width())),
                math.ceil(original.height() / max(1, canvas.winfo_height())),
            )
            canvas.picture = original.subsample(factor, factor)
            canvas.delete("all")
            canvas.create_image(
                canvas.winfo_width() // 2, canvas.winfo_height() // 2, image=canvas.picture
            )
        except (tk.TclError, OSError) as exc:
            canvas.delete("all")
            canvas.create_text(
                20, 20, anchor="nw", text=f"Could not load this chart: {exc}", width=650
            )

    choice.current(0)
    choice.bind("<<ComboboxSelected>>", render)
    canvas.bind("<Configure>", render)


def evidence_text(table):
    lines = [
        f"Original: {table['source_rows']:,} records | Copy: {table['copy_rows']:,} records",
        f"Record ID columns: original {table['source_keys']} | copy {table['copy_keys']}",
        f"Records compared with originals: {table['checked_rows']:,}",
        f"IDs absent from original: {table['missing_originals']:,}",
        f"Records with changed values: {table['changed_records']:,}",
        f"Duplicate ID groups: {table['duplicate_key_groups']}",
        "Table structure: "
        + (
            "matches" if not table["schema_differences"] else "; ".join(table["schema_differences"])
        ),
    ]
    if not table["copy_rows"]:
        lines.append("EMPTY COPY TABLE: no records were available to verify or demonstrate.")
    lines += ["", "CONNECTIONS TO OTHER RECORDS"]
    if not table["relationships"]:
        lines.append(
            "No declared relationships in this table. Relationship preservation is not applicable."
        )
    for r in table["relationships"]:
        lines.append(f"{r['from']} -> {r['parent_table']}.{', '.join(r['parent_columns'])}")
        lines.append(
            f"  References checked: {r['checked']:,} | Missing related records: {r['missing_parents'] if r['missing_parents'] is not None else 'NOT VERIFIED'}"
        )
        if not r["checked"]:
            lines.append("  No non-null references available to demonstrate this connection.")
        for example in r["examples"]:
            lines.append(f"  Record ID {example['child_key']} points to {example['reference']}")
            lines.append(f"  Related record found in copy: {example['parent_in_copy']}")
    lines += ["", "BEFORE / AFTER EXAMPLES (up to 3; counts above cover every copied record)"]
    for index, example in enumerate(table["examples"], 1):
        lines.append(f"\nExample {index}")
        original, copy = example["original"], example["copy"]
        if original is None:
            lines.append("Original record NOT FOUND.")
        for column in dict.fromkeys([*(original or {}), *copy]):
            before = repr(original.get(column)) if original is not None else "MISSING"
            after = repr(copy.get(column))
            lines.append(f"  {column}: Original {before}  |  Copy {after}")
    return "\n".join(lines)


def show_evidence(root, report):
    dialog = tk.Toplevel(root)
    dialog.title("Verify my copy - records and connections")
    dialog.geometry("950x720")
    ttk.Label(
        dialog,
        text="All inspected checks passed" if report["passed"] else "Some checks need attention",
        font=("Segoe UI", 14, "bold"),
        padding=10,
    ).pack(anchor="w")
    note = (
        f"Source: {report['source']} | Copy: {report['copy']}\n"
        f"Inspected at {report['inspected_at']}. This checks current contents; avoid editing either database during verification.\n"
        "Choose a table below to inspect records and their connections. Comparison mode verifies the last method's copy."
    )
    if report["file_input"]:
        note += "\nFile imports use generated IDs. CSV/Excel files do not declare database foreign keys."
    if report["missing_tables"] or report["extra_tables"]:
        note += (
            f"\nMissing tables: {report['missing_tables']} | Extra tables: {report['extra_tables']}"
        )
    ttk.Label(dialog, text=note, wraplength=880, padding=10).pack(fill="x")
    actions = ttk.Frame(dialog)
    actions.pack(side="bottom", fill="x", padx=12, pady=10)

    def save():
        path = filedialog.asksaveasfilename(
            parent=dialog,
            defaultextension=".json",
            initialfile="copy_verification.json",
            filetypes=[("Verification report", "*.json")],
        )
        if path:
            try:
                Path(path).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Could not save report", str(exc), parent=dialog)

    ttk.Button(actions, text="Save verification report", command=save).pack(side="left")
    ttk.Button(actions, text="Close", command=dialog.destroy).pack(side="right")
    choice = ttk.Combobox(
        dialog,
        state="readonly",
        values=[
            f"{t['table']} - {t['source_rows']:,} original / {t['copy_rows']:,} copied - "
            + ("checks passed" if t["passed"] else "needs attention")
            for t in report["tables"]
        ],
    )
    choice.pack(fill="x", padx=12, pady=8)
    pages = ttk.Notebook(dialog)
    pages.pack(fill="both", expand=True, padx=12)
    frame = ttk.Frame(pages)
    records = ttk.Frame(pages)
    connections = ttk.Frame(pages)
    pages.add(records, text="Compare records")
    pages.add(connections, text="Follow connections")
    pages.add(frame, text="All checks")
    ttk.Label(
        records,
        text="These examples show actual retained records. All copied records are checked, not just these examples.",
        wraplength=850,
        padding=8,
    ).pack(anchor="w")
    record_choice = ttk.Combobox(records, state="readonly")
    record_choice.pack(fill="x", padx=8, pady=8)
    record_grid = ttk.Treeview(
        records, columns=("field", "before", "after", "match"), show="headings"
    )
    for column, title, width in (
        ("field", "Field", 160),
        ("before", "Original value", 270),
        ("after", "Value in copy", 270),
        ("match", "Same?", 80),
    ):
        record_grid.heading(column, text=title)
        record_grid.column(column, width=width)
    record_scroll = ttk.Scrollbar(records, orient="vertical", command=record_grid.yview)
    record_grid.configure(yscrollcommand=record_scroll.set)
    record_scroll.pack(side="right", fill="y")
    record_grid.pack(fill="both", expand=True, padx=8, pady=8)
    relationship_text = tk.Text(connections, wrap="word", font=("Segoe UI", 11), padx=14, pady=14)
    relation_scroll = ttk.Scrollbar(connections, orient="vertical", command=relationship_text.yview)
    relationship_text.configure(yscrollcommand=relation_scroll.set)
    relation_scroll.pack(side="right", fill="y")
    relationship_text.pack(fill="both", expand=True)
    text = tk.Text(frame, wrap="word", font=("Consolas", 10), padx=10, pady=10)
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    text.pack(fill="both", expand=True)

    def display_record(event=None):
        record_grid.delete(*record_grid.get_children())
        table = report["tables"][choice.current()]
        if not table["examples"]:
            return
        example = table["examples"][record_choice.current()]
        original, copy = example["original"], example["copy"]
        for column in dict.fromkeys([*(original or {}), *copy]):
            same = (
                original is not None
                and column in original
                and column in copy
                and original[column] == copy[column]
            )
            record_grid.insert(
                "",
                "end",
                values=(
                    column,
                    str(original[column])
                    if original is not None and column in original
                    else "MISSING",
                    str(copy[column]) if column in copy else "MISSING",
                    "Yes" if same else "NO",
                ),
            )

    record_choice.bind("<<ComboboxSelected>>", display_record)

    def select(event=None):
        table = report["tables"][choice.current()]
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", evidence_text(table))
        text.configure(state="disabled")
        record_choice.configure(
            values=[
                "Record " + ", ".join(f"{k} = {example['copy'][k]}" for k in table["source_keys"])
                for example in table["examples"]
            ]
        )
        if table["examples"]:
            record_choice.current(0)
            display_record()
        else:
            record_choice.set("No copied records available to display")
            record_grid.delete(*record_grid.get_children())
        lines = []
        for r in table["relationships"]:
            lines += [
                f"{table['table']} connects to {r['parent_table']}",
                f"Connection: {r['from']} → {', '.join(r['parent_columns'])}",
                f"References checked: {r['checked']:,}",
                f"Missing related records: {r['missing_parents'] if r['missing_parents'] is not None else 'Not verified'}",
                "",
            ]
            if not r["checked"]:
                lines.append("No non-null references in this copy to demonstrate.\n")
            for example in r["examples"]:
                identity = ", ".join(f"{k} = {v}" for k, v in example["child_key"].items())
                reference = ", ".join(f"{k} = {v}" for k, v in example["reference"].items())
                lines += [f"Record {identity} points to {reference}."]
                if example["parent_in_copy"] is None:
                    lines.append("The required related record is MISSING from the copy.")
                else:
                    lines.append(f"The matching {r['parent_table']} record is present in the copy:")
                    lines.extend(f"    {k}: {v}" for k, v in example["parent_in_copy"].items())
                lines.append("")
        if not lines:
            lines = [
                "This table has no declared relationships.",
                "There are no database foreign keys to verify for this table.",
            ]
        relationship_text.configure(state="normal")
        relationship_text.delete("1.0", "end")
        relationship_text.insert("1.0", "\n".join(lines))
        relationship_text.configure(state="disabled")

    choice.bind("<<ComboboxSelected>>", select)
    if report["tables"]:
        choice.current(0)
        select()
