"""Inspectable records and chart windows for the desktop demo."""

import json
import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from safeslicesql.friendly_report import chart_data, export_report
from safeslicesql.record_browser import record_status
from safeslicesql.saved_results import load_summary, read_evidence
from safeslicesql.user_errors import show_error
from safeslicesql.view_tasks import background


def chart_files(folder):
    return sorted((Path(folder) / "plots").glob("*.png"))


def record_label(record):
    row = record.get("copy") or record.get("original") or {}
    name = next((str(v) for k, v in row.items() if k.lower() in {"name", "title", "full_name"}), "")
    identity = ", ".join(f"{k}={v}" for k, v in record.get("keys", {}).items())
    return f"{record['table']}: {name + ' (' + identity + ')' if name and identity else name or identity}"[
        :160
    ]


def snapshot_record(table, example):
    record = {
        **example,
        "table": table["table"],
        "keys": {k: example["copy"][k] for k in table["source_keys"]},
        "status": record_status(example["original"], example["copy"]),
        "connections": [],
    }
    for relation in table["relationships"]:
        link = next((e for e in relation["examples"] if e["child_key"] == record["keys"]), None)
        if link:
            record["connections"].append(
                {
                    "table": relation["parent_table"],
                    "keys": {},
                    "reference": link["reference"],
                    "original": link.get("parent_in_original"),
                    "copy": link["parent_in_copy"],
                    "original_known": "parent_in_original" in link,
                    "required_by_copy": True,
                    "optional": False,
                }
            )
        else:
            record["connections"].append(
                {
                    "table": relation["parent_table"],
                    "keys": {},
                    "reference": {},
                    "original": None,
                    "copy": None,
                    "optional": True,
                    "required_by_copy": True,
                }
            )
    return record


def show_record_pair(root, record):
    dialog = tk.Toplevel(root)
    dialog.title("Original and copy — " + record_label(record))
    dialog.geometry("820x500")
    ttk.Label(dialog, text=record_label(record), font=("Segoe UI", 14, "bold"), padding=12).pack(
        anchor="w"
    )
    ttk.Button(dialog, text="Close", command=dialog.destroy).pack(side="bottom", pady=10)
    grid = ttk.Treeview(dialog, columns=("field", "original", "copy"), show="headings")
    for key, title in (
        ("field", "Field"),
        ("original", "Original value"),
        ("copy", "Value in copy"),
    ):
        grid.heading(key, text=title)
        grid.column(key, width=250)
    scroll = ttk.Scrollbar(dialog, orient="vertical", command=grid.yview)
    grid.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    grid.pack(fill="both", expand=True, padx=10)
    original, copy = record.get("original"), record.get("copy")
    for column in dict.fromkeys([*(original or {}), *(copy or {})]):
        before = (
            str(original.get(column))
            if original is not None
            else (
                "NOT IN ORIGINAL"
                if record.get("original_known", True)
                else "Not captured in this older snapshot"
            )
        )
        grid.insert(
            "",
            "end",
            values=(column, before, str(copy.get(column)) if copy is not None else "NOT IN COPY"),
        )


def draw_connections(canvas, record, root):
    canvas.delete("all")
    canvas.create_text(
        20,
        20,
        text="Click either record card to compare its original and copied values.",
        anchor="nw",
    )
    links = record.get("connections", [])
    if not links:
        canvas.create_text(
            20,
            60,
            text="No declared relationships for this table. No foreign keys to demonstrate.",
            anchor="nw",
        )
    for index, link in enumerate(links):
        y = 65 + index * 170
        missing = link["required_by_copy"] and not link["optional"] and link["copy"] is None
        caption = (
            "Optional / no reference"
            if link["optional"]
            else "Required parent MISSING"
            if missing
            else "Related record is in copy"
            if link["copy"] is not None
            else "Outside copy; child is also outside copy"
        )
        for side, x, item in (("child", 20, record), ("parent", 510, link)):
            tag = f"{side}{index}"
            canvas.create_rectangle(
                x,
                y,
                x + 300,
                y + 85,
                fill="#fff0f0" if missing and side == "parent" else "#edf2fc",
                outline="#2456d9",
                tags=tag,
            )
            canvas.create_text(
                x + 15, y + 15, text=record_label(item), width=270, anchor="nw", tags=tag
            )
            canvas.create_text(
                x + 15, y + 52, text="Open before / after", anchor="nw", tags=tag, fill="#2456d9"
            )
            canvas.tag_bind(
                tag, "<Button-1>", lambda event, item=item: show_record_pair(root, item)
            )
        canvas.create_line(325, y + 40, 505, y + 40, arrow="last", width=3, fill="#2456d9")
        canvas.create_text(
            415, y + 5, text=", ".join(f"{k}={v}" for k, v in link["reference"].items()), width=175
        )
        canvas.create_text(
            20, y + 108, text=caption, anchor="nw", fill="#b42318" if missing else "#18243c"
        )
    canvas.configure(scrollregion=(0, 0, 840, max(250, 80 + 170 * len(links))))


def save_readable(root, folder):
    path = filedialog.asksaveasfilename(
        parent=root,
        initialfile="SafeSliceSQL report.html",
        defaultextension=".html",
        filetypes=[("Readable report (open in a browser; print to PDF)", "*.html")],
    )
    if path:
        try:
            export_report(folder, path)
            messagebox.showinfo(
                "Report saved",
                "Open the saved HTML file in a browser. Use Print / Save as PDF for a PDF copy.",
                parent=root,
            )
        except (OSError, ValueError, KeyError) as exc:
            show_error(root, str(exc), retry=lambda: save_readable(root, folder))


def show_saved_results(root, folder, *, verify, evidence, settings=None):
    summary = load_summary(folder)
    dialog = tk.Toplevel(root)
    dialog.title("Previous copy — " + summary["source"])
    ttk.Label(
        dialog,
        text="Saved results — no new copy created",
        font=("Segoe UI", 14, "bold"),
        padding=14,
    ).pack(anchor="w")
    ttk.Label(
        dialog,
        text=f"Source: {summary['source']}\nCopy: {summary['target']}\nRecorded: {summary['created_at']}\n"
        f"Result: {'Accepted' if summary['ready'] else 'Needs attention'}\n"
        f"Original records: {summary['source_rows']:,}\nCopied records: {summary['copy_rows'] if summary['copy_rows'] is not None else 'Not recorded'}",
        padding=14,
        wraplength=650,
    ).pack(anchor="w")
    ttk.Label(
        dialog,
        text="Charts and saved evidence work offline. Verify again reads the current databases using your current connection settings.",
        padding=14,
        wraplength=650,
    ).pack(anchor="w")
    for label, command in (
        ("View charts", lambda: show_charts(dialog, folder)),
        ("Open saved verification", evidence),
        ("Verify again", verify),
        ("Export readable report", lambda: save_readable(dialog, folder)),
    ):
        ttk.Button(dialog, text=label, command=command).pack(fill="x", padx=14, pady=5)
    if settings:
        ttk.Button(dialog, text="Connection settings", command=settings).pack(
            fill="x", padx=14, pady=5
        )
    ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=14)


def show_charts(root, folder):
    files = chart_files(folder)
    try:
        summary, evidence = load_summary(folder), read_evidence(folder)
        simple = chart_data(summary, evidence)
    except (OSError, ValueError, KeyError):
        simple = None
    if not files and simple is None:
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
        dialog,
        state="readonly",
        values=(
            ["Original records vs copied records", "Records kept per table", "Broken connections"]
            if simple
            else []
        )
        + ["Advanced: " + p.stem.replace("_", " ").title() for p in files],
    )
    choice.pack(fill="x", padx=12)
    ttk.Button(dialog, text="Close", command=dialog.destroy).pack(side="bottom", pady=8)
    ttk.Label(
        dialog,
        text="Charts summarize measured runs. Use Verify my copy to inspect actual records.",
        wraplength=820,
    ).pack(side="bottom", pady=8)
    canvas = tk.Canvas(dialog, background="white", highlightthickness=0)
    scroll = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    canvas.pack(fill="both", expand=True, padx=12, pady=12)
    images = {}

    def render(event=None):
        try:
            index = choice.current()
            canvas.delete("all")
            if simple and index < 3:
                canvas.yview_moveto(0)
                if index == 0:
                    items = [("Original", simple["original"]), ("Copied", simple["copied"])]
                elif index == 1:
                    items = [(t["table"], t["copy_rows"]) for t in simple["tables"]]
                else:
                    items = [("Missing required references", simple["missing"])]
                maximum = max([v for _, v in items if v is not None] + [1])
                width = max(300, canvas.winfo_width())
                canvas.create_text(
                    20, 20, anchor="nw", text=choice.get(), font=("Segoe UI", 16, "bold")
                )
                for i, (label, value) in enumerate(items):
                    y = 70 + i * 65
                    canvas.create_text(20, y, text=label, anchor="nw", width=210)
                    if value is not None:
                        canvas.create_rectangle(
                            240,
                            y,
                            240 + value / maximum * max(20, width - 350),
                            y + 28,
                            fill="#c03434" if index == 2 and value else "#2456d9",
                            outline="",
                        )
                    canvas.create_text(
                        width - 90,
                        y + 14,
                        text=f"{value:,}" if value is not None else "Not verified",
                        anchor="w",
                    )
                note = (
                    (
                        "No missing required references found."
                        if simple["missing"] == 0
                        else "A missing parent is a broken connection; an excluded record alone is not."
                    )
                    if index == 2
                    else "Counts describe the saved run / verification, not future edits to either database."
                )
                if index == 2 and simple["references"] is None and evidence:
                    note = "No declared relationships were available to verify. This is not proof of FK preservation."
                y = 90 + 65 * len(items)
                canvas.create_text(20, y, text=note, anchor="nw", width=width - 40)
                canvas.configure(scrollregion=(0, 0, width, max(y + 70, canvas.winfo_height())))
                return
            index -= 3 if simple else 0
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
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), canvas.winfo_height()))
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


def show_evidence(
    root, report, *, search=None, saved=False, refresh=None, folder=None, settings=None
):
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
        f"Inspected at {report['inspected_at']}. Counts reflect that inspection; live search reads current contents.\n"
        "Choose a table below to inspect records and their connections. Comparison mode verifies the last method's copy."
    )
    if saved:
        note = "SAVED SNAPSHOT — opening this view does not recheck the databases.\n" + note
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
    if folder:
        ttk.Button(
            actions, text="Export readable report", command=lambda: save_readable(dialog, folder)
        ).pack(side="left", padx=8)
    if refresh:
        ttk.Button(actions, text="Verify again", command=refresh).pack(side="left", padx=8)
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
    find = ttk.Frame(records)
    find.pack(fill="x", padx=8, pady=6)
    field = ttk.Combobox(find, state="readonly", width=18)
    field.pack(side="left")
    query = ttk.Entry(find, width=24)
    query.pack(side="left", padx=5)
    exact = tk.BooleanVar(value=False)
    ttk.Checkbutton(find, text="Exact match", variable=exact).pack(side="left")
    notice = tk.StringVar(
        value="Select a sample, or search the current original and copy by a field such as ID or Name."
    )
    ttk.Label(records, textvariable=notice, wraplength=850, padding=8).pack(fill="x")
    active = {"records": [], "offset": 0, "token": 0}
    page_note = tk.StringVar(
        value="Saved examples. Live search is read-only and does not regenerate the copy."
    )
    ttk.Label(records, textvariable=page_note, wraplength=850, padding=8).pack(
        side="bottom", fill="x"
    )
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
    relationship_text = tk.Canvas(connections, background="white", highlightthickness=0)
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
        if not active["records"] or record_choice.current() < 0:
            return
        example = active["records"][record_choice.current()]
        original, copy = example["original"], example["copy"]
        notice.set(
            example.get("status", record_status(original, copy))
            + (
                ". This record is outside the current copy. Subsetting keeps only some records; exact selection reasons are not logged. Absence alone is not a broken connection. Later edits can also change membership."
                if copy is None and original is not None
                else ". Original and copy values are shown below."
            )
        )
        draw_connections(relationship_text, example, dialog)
        for column in dict.fromkeys([*(original or {}), *(copy or {})]):
            same = (
                original is not None
                and column in original
                and copy is not None
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
                    str(copy[column]) if copy is not None and column in copy else "NOT IN COPY",
                    "Yes" if same else "NO",
                ),
            )

    record_choice.bind("<<ComboboxSelected>>", display_record)

    def populate(records):
        active["records"] = records
        record_choice.configure(values=[record_label(r) + " — " + r["status"] for r in records])
        if records:
            record_choice.current(0)
            display_record()
        else:
            record_choice.set("No matching records")
            record_grid.delete(*record_grid.get_children())
            relationship_text.delete("all")
            notice.set("No matches on this page. Try another field, a shorter search, or Previous.")

    def find_records(offset=0):
        if search is None:
            notice.set(
                "Live search is unavailable. Reopen this saved job with valid connection settings."
            )
            return
        active["token"] += 1
        token = active["token"]
        table = report["tables"][choice.current()]["table"]
        selected_field, phrase, exact_match = field.get(), query.get(), exact.get()
        notice.set("Searching current databases...")
        search_button.configure(state="disabled")

        def done(value):
            search_button.configure(state="normal")
            if token != active["token"]:
                return
            active["offset"] = offset
            populate(value["records"])
            next_button.configure(state="normal" if value["more"] else "disabled")
            previous_button.configure(state="normal" if offset else "disabled")
            page_note.set(
                f"Live lookup at {value['inspected_at']} — page {offset // 25 + 1}; up to 25 matches per database. Snapshot totals have not been refreshed."
            )

        def failed(message):
            search_button.configure(state="normal")
            notice.set("Search could not finish. The saved evidence remains available.")
            show_error(dialog, message, retry=lambda: find_records(offset), settings=settings)

        background(
            dialog,
            lambda: search(table, selected_field, phrase, exact=exact_match, offset=offset),
            done,
            failed,
        )

    search_button = ttk.Button(
        find, text="Search", command=find_records, state="normal" if search else "disabled"
    )
    search_button.pack(side="left", padx=5)
    previous_button = ttk.Button(
        find,
        text="Previous",
        state="disabled",
        command=lambda: find_records(max(0, active["offset"] - 25)),
    )
    previous_button.pack(side="left")
    next_button = ttk.Button(
        find, text="Next", state="disabled", command=lambda: find_records(active["offset"] + 25)
    )
    next_button.pack(side="left")
    query.bind("<Return>", lambda event: find_records())

    def select(event=None):
        active["token"] += 1
        active["offset"] = 0
        table = report["tables"][choice.current()]
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", evidence_text(table))
        text.configure(state="disabled")
        columns = table.get("columns") or list(
            dict.fromkeys(k for e in table["examples"] for k in e["copy"])
        )
        field.configure(values=columns)
        field.set(
            table["source_keys"][0] if table["source_keys"] else (columns[0] if columns else "")
        )
        previous_button.configure(state="disabled")
        next_button.configure(state="disabled")
        page_note.set(
            "Saved examples. Search reads current databases; Verify again refreshes totals."
        )
        populate([snapshot_record(table, example) for example in table["examples"]])

    choice.bind("<<ComboboxSelected>>", select)
    if report["tables"]:
        choice.current(0)
        select()
