"""A plain-language desktop flow for creating smaller database copies."""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4

from safeslicesql.config import AppConfig, DatabaseConfig, load_config
from safeslicesql.copy_evidence import collect_evidence
from safeslicesql.desktop_service import CopyRequest, create_copy
from safeslicesql.input_picker import available_databases
from safeslicesql.input_preview import preview_input
from safeslicesql.jobs import owned_jobs, read_job, recover_jobs
from safeslicesql.result_views import show_charts, show_evidence
from safeslicesql.runtime import Limits, OperationInterrupted, execution_context
from safeslicesql.sampler import METHODS
from safeslicesql.security import safe_error

KINDS = {
    "MySQL database": "mysql",
    "CSV file": "csv",
    "CSV folder": "folder",
    "Excel workbook": "xlsx",
}


class Desktop:
    def __init__(self, root: tk.Tk, config: AppConfig):
        self.root, self.config = root, config
        self.events = queue.Queue()
        self.busy = False
        self.cancellation = threading.Event()
        self.outcome = None
        self.input_preview = None
        self.preview_pending = None
        self.preview_loading = False
        self.kind = tk.StringVar(value="CSV file")
        self.source = tk.StringVar()
        self.budget = tk.IntVar(value=10)
        self.method = tk.StringVar(value="workload-aware")
        self.seed = tk.StringVar(value="42")
        self.compare = tk.BooleanVar(value=False)
        self.workload = tk.StringVar()
        self.min_survival = tk.StringVar(value="100")
        self.min_retention = tk.StringVar(value="0")
        self.required = tk.StringVar()
        self.status = tk.StringVar(value="Choose your data to get started.")
        self.summary = tk.StringVar(value="Your smaller copy and checks will appear here.")
        self.details = tk.StringVar()
        root.title("SafeSliceSQL — Create a smaller data copy")
        root.geometry("1000x780")
        root.minsize(860, 680)
        root.configure(bg="#f4f6fa")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f6fa")
        style.configure("TLabel", background="#f4f6fa", foreground="#18243c", font=("Segoe UI", 11))
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Step.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("Muted.TLabel", foreground="#526079", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 11), padding=(12, 8))
        style.configure(
            "Primary.TButton",
            foreground="white",
            background="#2456d9",
            font=("Segoe UI", 12, "bold"),
        )
        style.map("Primary.TButton", background=[("active", "#163eaa"), ("disabled", "#9cadd5")])
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=28)
        style.configure("TNotebook.Tab", font=("Segoe UI", 11), padding=(18, 10))
        style.map(
            "TNotebook.Tab",
            padding=[("selected", (18, 10)), ("!selected", (18, 10))],
            background=[("selected", "#f4f6fa"), ("!selected", "#e4e9f2")],
            foreground=[("selected", "#2456d9"), ("!selected", "#526079")],
        )
        self._build()
        self.source.trace_add("write", self._source_changed)
        self._source_changed()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)
        interrupted = recover_jobs(Path("results"))
        if interrupted:
            self.status.set(
                f"Found {len(interrupted)} interrupted job(s). Open Previous jobs in Options; restart from the original input."
            )

    def _build(self):
        header = ttk.Frame(self.root, padding=(24, 16, 24, 12))
        header.pack(fill="x")
        ttk.Label(header, text="SafeSliceSQL", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Choose your data. Create a smaller copy. Check the results.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        footer = ttk.Frame(self.root, padding=(24, 12, 24, 16))
        footer.pack(side="bottom", fill="x")
        self.action_row = ttk.Frame(footer)
        self.action_row.pack(fill="x")
        self.run_button = ttk.Button(
            self.action_row, text="Create smaller copy", style="Primary.TButton", command=self._run
        )
        self.run_button.pack(side="right")
        self.cancel_button = ttk.Button(
            self.action_row, text="Cancel", state="disabled", command=self._cancel
        )
        self.cancel_button.pack(side="right", padx=(0, 10))
        ttk.Label(
            self.action_row, text="Your original data stays intact.", style="Muted.TLabel"
        ).pack(side="left")
        self.progress = ttk.Progressbar(footer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(12, 6))
        self.status_label = ttk.Label(
            footer, textvariable=self.status, style="Muted.TLabel", wraplength=800
        )
        self.status_label.pack(anchor="w")
        footer.bind(
            "<Configure>", lambda e: self.status_label.configure(wraplength=max(200, e.width - 48))
        )

        self.notebook = ttk.Notebook(self.root, padding=(24, 0))
        self.notebook.pack(fill="both", expand=True)
        self._page_canvases = {}

        def page(title):
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text=title)
            canvas = tk.Canvas(tab, background="#f4f6fa", highlightthickness=0)
            scroll = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
            scroll.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            canvas.configure(yscrollcommand=scroll.set)
            content = ttk.Frame(canvas, padding=(16, 18))
            window = canvas.create_window((0, 0), window=content, anchor="nw")
            content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
            self._page_canvases[str(tab)] = canvas
            return tab, content

        self.create_page, page_body = page("1  Create a copy")
        self.options_page, options = page("2  Options")
        self.results_page, results = page("3  Results")
        self.canvas = self._page_canvases[str(self.create_page)]
        self.root.bind("<MouseWheel>", self._scroll_page, add="+")
        self.notebook.enable_traversal()
        for number, tab in enumerate((self.create_page, self.options_page, self.results_page), 1):
            self.root.bind(
                f"<Alt-Key-{number}>", lambda e, target=tab: self.notebook.select(target)
            )

        def heading(parent, title, hint):
            ttk.Label(parent, text=title, style="Step.TLabel").pack(anchor="w")
            ttk.Label(parent, text=hint, style="Muted.TLabel", wraplength=720).pack(
                anchor="w", pady=(4, 12)
            )

        heading(
            page_body, "Choose your data", "Select a file, folder, or an existing MySQL database."
        )
        row = ttk.Frame(page_body)
        row.pack(fill="x")
        row.columnconfigure(1, weight=1)
        ttk.Label(row, text="Data type").grid(row=0, column=0, sticky="w", pady=(0, 5))
        ttk.Label(row, text="Selected source").grid(row=0, column=1, sticky="w", pady=(0, 5))
        self.kind_box = ttk.Combobox(
            row, textvariable=self.kind, values=list(KINDS), state="readonly", width=19
        )
        self.kind_box.grid(row=1, column=0, sticky="ew", padx=(0, 12))
        self.kind_box.bind("<<ComboboxSelected>>", lambda _: self._change_kind())
        self.source_box = ttk.Combobox(row, textvariable=self.source, state="disabled", width=24)
        self.source_box.grid(row=1, column=1, sticky="ew", padx=(0, 12))
        self.browse = ttk.Button(row, text="Choose file...", command=self._browse)
        self.browse.grid(row=1, column=2, sticky="e")
        self.source_note = ttk.Label(
            page_body,
            text="CSV and Excel files need a header row. File imports use your MySQL connection.",
            style="Muted.TLabel",
            wraplength=720,
        )
        self.source_note.pack(anchor="w", pady=(8, 8))
        stats = ttk.Frame(page_body)
        stats.pack(fill="x", pady=(0, 14))
        self.input_stats = tk.StringVar(value="Choose a source to see its size.")
        ttk.Label(stats, textvariable=self.input_stats, wraplength=570).pack(
            side="left", fill="x", expand=True
        )
        self.inspect_button = ttk.Button(stats, text="Refresh stats", command=self._preview_source)
        self.inspect_button.pack(side="right", padx=(8, 0))
        self.table_button = ttk.Button(
            stats, text="Table details", command=self._input_details, state="disabled"
        )
        self.table_button.pack(side="right")
        heading(
            page_body,
            "How much should the copy keep?",
            "Choose a percentage of the original rows. Related records count toward this limit.",
        )
        self.budget_hint = tk.StringVar(value="Select a source to calculate the output row budget.")
        ttk.Label(
            page_body, textvariable=self.budget_hint, style="Muted.TLabel", wraplength=720
        ).pack(anchor="w", pady=(0, 8))
        controls = ttk.Frame(page_body)
        controls.pack(fill="x")
        self.slider = ttk.Scale(controls, from_=1, to=100, command=self._slide)
        self.slider.set(10)
        self.slider.pack(side="left", fill="x", expand=True, padx=(0, 20))
        self.percentage = ttk.Label(controls, text="10%", style="Step.TLabel", width=6)
        self.percentage.pack(side="right")
        presets = ttk.Frame(page_body)
        presets.pack(fill="x", pady=(10, 18))
        self.preset_buttons = []
        for value in (10, 25, 50, 100):
            button = ttk.Button(
                presets, text=f"{value}%", width=7, command=lambda n=value: self.slider.set(n)
            )
            button.pack(side="left", padx=(0, 8))
            self.preset_buttons.append(button)
        self.advanced_toggle = ttk.Button(
            page_body, text="Selection and quality options", command=self._toggle_advanced
        )
        self.advanced_toggle.pack(anchor="w")
        ttk.Label(
            page_body,
            text="The default settings are ready to use. Options let you compare methods or test your own SQL.",
            style="Muted.TLabel",
            wraplength=720,
        ).pack(anchor="w", pady=(10, 0))

        heading(
            options,
            "Customize your copy",
            "These settings are optional. Your choices stay in place when switching tabs.",
        )
        form = ttk.Frame(options)
        form.pack(fill="x")
        form.columnconfigure(0, weight=1, uniform="fields")
        form.columnconfigure(1, weight=1, uniform="fields")

        def field(label, widget, row, column):
            ttk.Label(form, text=label).grid(
                row=row * 2, column=column, sticky="w", padx=(0, 16), pady=(8, 5)
            )
            widget.grid(row=row * 2 + 1, column=column, sticky="ew", padx=(0, 16), pady=(0, 8))

        self.method_box = ttk.Combobox(
            form, textvariable=self.method, values=list(METHODS), state="readonly"
        )
        self.seed_box = ttk.Entry(form, textvariable=self.seed)
        field("Selection method", self.method_box, 0, 0)
        field("Repeatable seed", self.seed_box, 0, 1)
        self.compare_box = ttk.Checkbutton(
            options, text="Compare all three selection methods", variable=self.compare
        )
        self.compare_box.pack(anchor="w", pady=(4, 14))
        ttk.Separator(options).pack(fill="x", pady=(0, 12))
        ttk.Label(options, text="Query checks", style="Step.TLabel").pack(anchor="w")
        ttk.Label(
            options,
            text="Leave SQL blank to use automatic checks. Percentages below define what counts as a usable copy.",
            style="Muted.TLabel",
            wraplength=720,
        ).pack(anchor="w", pady=(4, 8))
        sql_row = ttk.Frame(options)
        sql_row.pack(fill="x")
        self.workload_box = ttk.Entry(sql_row, textvariable=self.workload)
        self.workload_box.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.workload_button = ttk.Button(
            sql_row, text="Choose SQL...", command=self._pick_workload
        )
        self.workload_button.pack(side="right")
        form = ttk.Frame(options)
        form.pack(fill="x")
        form.columnconfigure(0, weight=1, uniform="fields")
        form.columnconfigure(1, weight=1, uniform="fields")
        self.survival_box = ttk.Entry(form, textvariable=self.min_survival)
        self.retention_box = ttk.Entry(form, textvariable=self.min_retention)
        field("Queries that must still return results (%)", self.survival_box, 0, 0)
        field("Exact result rows to retain (%)", self.retention_box, 0, 1)
        self.required_box = ttk.Entry(form, textvariable=self.required)
        field("Required query IDs (optional, e.g. Q1,Q5)", self.required_box, 1, 0)
        links = ttk.Frame(options)
        links.pack(fill="x", pady=(12, 0))
        self.connection_button = ttk.Button(
            links, text="MySQL connections...", command=self._connections
        )
        self.connection_button.pack(side="left", padx=(0, 10))
        self.history_button = ttk.Button(links, text="Previous jobs...", command=self._history)
        self.history_button.pack(side="left")

        heading(
            results,
            "Your copy and checks",
            "Completed runs appear here automatically. Reports include all compared methods.",
        )
        ttk.Label(results, textvariable=self.summary, style="Step.TLabel", wraplength=720).pack(
            anchor="w", pady=(0, 14)
        )
        self.results = ttk.Treeview(results, columns=("check", "result"), show="headings", height=6)
        self.results.heading("check", text="Check")
        self.results.heading("result", text="Outcome")
        self.results.column("check", width=340, minwidth=230)
        self.results.column("result", width=340, minwidth=230)
        self.results.pack(fill="x")
        buttons = ttk.Frame(results)
        buttons.pack(fill="x", pady=(16, 8))
        self.report_button = ttk.Button(
            buttons, text="Open reports", state="disabled", command=lambda: self._open("reports")
        )
        self.report_button.pack(side="left", padx=(0, 10))
        self.chart_button = ttk.Button(
            buttons, text="View charts", state="disabled", command=lambda: self._open("charts")
        )
        self.chart_button.pack(side="left", padx=(0, 10))
        self.verify_button = ttk.Button(
            buttons, text="Verify my copy", state="disabled", command=self._verify_copy
        )
        self.verify_button.pack(side="left", padx=(0, 10))
        self.detail_button = ttk.Button(
            buttons,
            text="Run details",
            command=lambda: messagebox.showinfo(
                "Run details",
                self.details.get() or "Create a copy first to see its details.",
                parent=self.root,
            ),
        )
        self.detail_button.pack(side="left")
        ttk.Button(
            results,
            text="Back to your data",
            command=lambda: self.notebook.select(self.create_page),
        ).pack(anchor="w", pady=(10, 0))

    def _scroll_page(self, event):
        if event.widget.winfo_toplevel() != self.root or event.widget.winfo_class() in {
            "TCombobox",
            "Treeview",
            "Text",
            "Listbox",
        }:
            return
        if event.delta:
            steps = max(1, abs(event.delta) // 120)
            self._page_canvases[self.notebook.select()].yview_scroll(
                -steps if event.delta > 0 else steps, "units"
            )
            return "break"

    def _slide(self, value):
        self.budget.set(round(float(value)))
        if hasattr(self, "percentage"):
            self.percentage.configure(text=f"{self.budget.get()}%")
        self._update_budget_hint()

    def _toggle_advanced(self):
        self.notebook.select(self.options_page)

    def _source_changed(self, *_):
        if self.preview_pending is not None:
            self.root.after_cancel(self.preview_pending)
            self.preview_pending = None
        self.input_preview = None
        self.input_stats.set(
            "Choose a source to see its size."
            if not self.source.get()
            else "Reading input statistics..."
        )
        self.table_button.configure(state="disabled")
        self.run_button.configure(state="disabled")
        self._update_budget_hint()
        if self.source.get() and not self.busy:
            self.preview_pending = self.root.after(300, self._preview_source)

    def _preview_source(self):
        self.preview_pending = None
        if self.busy or not self.source.get():
            return
        self.input_preview = None
        self._update_budget_hint()
        self.input_stats.set("Reading input statistics...")
        self.preview_loading = True
        identity = (KINDS[self.kind.get()], self.source.get())
        config = self.config
        self._start(lambda: ("preview", (identity, preview_input(config, *identity))))

    def _update_budget_hint(self):
        if not hasattr(self, "budget_hint"):
            return
        if self.input_preview is None:
            self.budget_hint.set("Select a source to calculate the output row budget.")
        else:
            planned = self.input_preview.row_budget(self.budget.get())
            self.budget_hint.set(
                f"{self.budget.get()}% of {self.input_preview.total_rows:,} rows: up to {planned:,} output rows. Final count may be lower."
            )

    def _input_details(self):
        if self.input_preview is None:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Input statistics")
        dialog.geometry("680x440")
        dialog.minsize(540, 360)
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(side="bottom", pady=12)
        note = "Input is checked again when you create a copy. File column counts exclude the generated record ID."
        if self.input_preview.warnings:
            note += "\nSchema notes: " + "; ".join(self.input_preview.warnings)
        ttk.Label(dialog, text=note, wraplength=620).pack(
            side="bottom", anchor="w", padx=16, pady=12
        )
        ttk.Label(
            dialog,
            text="Current input size - inspected without creating a copy",
            style="Step.TLabel",
        ).pack(anchor="w", padx=16, pady=16)
        frame = ttk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=16)
        tree = ttk.Treeview(frame, columns=("table", "rows", "columns"), show="headings")
        for name, title, width in (
            ("table", "Table / sheet", 320),
            ("rows", "Data rows", 130),
            ("columns", "Columns", 100),
        ):
            tree.heading(name, text=title)
            tree.column(name, width=width, anchor="w" if name == "table" else "e")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        for name, rows, columns in self.input_preview.tables:
            tree.insert("", "end", values=(name, f"{rows:,}", columns))

    def _change_kind(self):
        self.source.set("")
        self.source_box.configure(
            values=[], state="readonly" if self.kind.get() == "MySQL database" else "disabled"
        )
        self.browse.configure(
            text="Find databases"
            if self.kind.get() == "MySQL database"
            else "Choose folder…"
            if self.kind.get() == "CSV folder"
            else "Choose file…"
        )
        self.source_note.configure(
            text="Choose a database from the configured source server."
            if self.kind.get() == "MySQL database"
            else "CSV and Excel files need column names in their first row. Each file or sheet becomes a table."
        )

    def _browse(self):
        if self.kind.get() == "MySQL database":
            self._start(lambda: ("databases", available_databases(self.config)))
            return
        if self.kind.get() == "CSV folder":
            value = filedialog.askdirectory(parent=self.root, mustexist=True)
        else:
            types = (
                [("Excel workbook", "*.xlsx")]
                if self.kind.get() == "Excel workbook"
                else [("CSV file", "*.csv")]
            )
            value = filedialog.askopenfilename(parent=self.root, filetypes=types)
        if value:
            self.source.set(value)
            self.status.set("Data selected. Choose how much to keep, then create your copy.")

    def _run(self):
        if self.input_preview is None or not self.input_preview.total_rows:
            messagebox.showinfo(
                "Check your input",
                "Preview a nonempty input before creating its copy.",
                parent=self.root,
            )
            self.notebook.select(self.create_page)
            return
        try:
            request = CopyRequest(
                KINDS[self.kind.get()],
                self.source.get(),
                self.budget.get(),
                self.method.get(),
                int(self.seed.get()),
                self.compare.get(),
                workload=self.workload.get().strip() or None,
                min_query_survival=float(self.min_survival.get()) / 100,
                min_result_retention=float(self.min_retention.get()) / 100,
                required_queries=tuple(
                    value.strip() for value in self.required.get().split(",") if value.strip()
                ),
            )
            request.validate()
        except ValueError as exc:
            messagebox.showinfo("Check your choices", str(exc), parent=self.root)
            return
        self.outcome = None
        self.report_button.configure(state="disabled")
        self.chart_button.configure(state="disabled")
        self.verify_button.configure(state="disabled")
        self.results.delete(*self.results.get_children())
        self.summary.set("Creating your smaller copy…")
        self.details.set("")
        output = Path("results/desktop") / uuid4().hex[:12]
        self._start(
            lambda: (
                "copy",
                create_copy(
                    self.config,
                    request,
                    output,
                    lambda text: self.events.put(("status", text)),
                    cancellation=self.cancellation,
                ),
            )
        )

    def _start(self, task):
        if self.busy:
            return
        self.busy = True
        self.cancellation.clear()
        self._lock(True)
        self.progress.start(12)
        self.status.set("Working…")

        def worker():
            try:
                with execution_context(Limits.from_env(), self.cancellation):
                    self.events.put(task())
            except OperationInterrupted as exc:
                self.events.put(("stopped", safe_error(exc)))
            except Exception as exc:
                self.events.put(("error", safe_error(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _lock(self, locked):
        self.verify_button.configure(
            state="normal" if self.outcome is not None and not locked else "disabled"
        )
        for widget in (
            *self.preset_buttons,
            self.inspect_button,
            self.browse,
            self.slider,
            self.advanced_toggle,
            self.run_button,
            self.seed_box,
            self.compare_box,
            self.connection_button,
            self.workload_box,
            self.workload_button,
            self.survival_box,
            self.retention_box,
            self.required_box,
            self.history_button,
        ):
            widget.configure(state="disabled" if locked else "normal")
        self.table_button.configure(
            state="normal" if self.input_preview and not locked else "disabled"
        )
        self.run_button.configure(
            state="normal"
            if self.input_preview and self.input_preview.total_rows and not locked
            else "disabled"
        )
        self.cancel_button.configure(state="normal" if locked else "disabled")
        for widget in (self.kind_box, self.method_box):
            widget.configure(state="disabled" if locked else "readonly")
        self.source_box.configure(
            state="disabled" if locked or self.kind.get() != "MySQL database" else "readonly"
        )

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(value)
                    continue
                self.busy = False
                self.progress.stop()
                self._lock(False)
                if kind == "databases":
                    self.source_box.configure(values=value)
                    if value:
                        self.source.set(value[0])
                    self.status.set(
                        "Choose a database, then set the size of your copy."
                        if value
                        else "No databases are visible. Check the source connection in Advanced options."
                    )
                elif kind == "preview":
                    identity, preview = value
                    self.preview_loading = False
                    if identity == (KINDS[self.kind.get()], self.source.get()):
                        self.input_preview = preview
                        self.input_stats.set(
                            f"Rows: {preview.total_rows:,}  |  Tables / sheets: {len(preview.tables)}  |  Columns: {sum(t[2] for t in preview.tables)}"
                        )
                        self._update_budget_hint()
                        self._lock(False)
                        self.status.set(
                            "Input inspected. Choose how much to keep, then create your copy."
                            if preview.total_rows
                            else "This input contains no data rows. Choose another source."
                        )
                elif kind == "stopped":
                    if getattr(self, "preview_loading", False):
                        self.preview_loading = False
                        self.input_stats.set("Inspection stopped. Refresh stats to try again.")
                    self.summary.set("Operation stopped. No copy is accepted for use.")
                    self.status.set(value)
                    self.details.set(
                        value
                        + "\nAny target committed before stopping needs review. Use Previous jobs in Options to find its journal."
                    )
                elif kind == "copy":
                    self._show_outcome(value)
                elif kind == "evidence":
                    self.status.set(
                        "Verification finished. Inspect the records and connections in the opened window."
                    )
                    show_evidence(self.root, value)
                else:
                    if getattr(self, "preview_loading", False):
                        self.preview_loading = False
                        self.input_stats.set(
                            "Could not inspect input. See Run details for the reason."
                        )
                    self.status.set(
                        "Could not complete this action. Open Details for the reason, or check Connection settings."
                    )
                    self.summary.set("No completed copy to show.")
                    self.details.set(value)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _show_outcome(self, outcome):
        if hasattr(self, "notebook"):
            self.notebook.select(self.results_page)
        self.outcome = outcome
        runs = outcome.results.runs
        if not runs:
            self.status.set("No copy was produced. Open the results folder for details.")
            return
        run = runs[-1]
        validation = run.validation
        original = sum(outcome.prepared.tables.values())
        actual = validation.actual_rows if validation else 0
        passed = (
            outcome.results.failed_runs == 0
            and run.success
            and all(item.acceptance is not None and item.acceptance.passed for item in runs)
        )
        label = (
            outcome.request.source
            if outcome.request.kind == "mysql"
            else Path(outcome.request.source).name
        )
        self.summary.set(
            f"{'Copy ready' if passed else 'Copy needs attention'} for {label}: {actual:,} rows from {original:,} original rows."
        )
        checks = [
            (
                "Rows in the smaller copy",
                f"{actual:,} / {run.selection.budget:,} allowed"
                if run.selection
                else "Selection did not complete",
            ),
            (
                "Required related records",
                "Included"
                if validation and validation.is_valid and validation.fk_violations == 0
                else "Could not verify",
            ),
            (
                "Unique record IDs and table structure",
                "Checks passed"
                if validation and validation.schema_compatible and validation.pk_unique
                else "Needs attention",
            ),
            (
                "Original data",
                "Unchanged — fingerprint matched"
                if validation and validation.source_unchanged
                else "Could not verify",
            ),
            (
                "Demonstration queries returning results",
                f"{sum(q.survived for q in run.workload.per_query_results)} of {run.workload.survival_denominator}"
                if run.workload
                else "Not evaluated",
            ),
        ]
        checks.append(
            (
                "Query quality requirements",
                "Passed" if run.acceptance and run.acceptance.passed else "Rejected / not assessed",
            )
        )
        for check in checks:
            self.results.insert("", "end", values=check)
        self.status.set(
            "Completed. Your separate copy is stored in MySQL."
            if passed
            else "Some checks failed. See Details and reports before using this copy."
        )
        self.report_button.configure(state="normal")
        self.chart_button.configure(state="normal")
        self.verify_button.configure(state="normal")
        comparisons = "\n".join(
            f"{r.method}: {'passed' if r.success else 'failed'}; query survival {r.workload.query_survival_rate:.1%}"
            if r.workload
            else f"{r.method}: workload unavailable"
            for r in runs
        )
        self.details.set(
            f"Source: {outcome.prepared.source.database}\nSeparate copy: {outcome.prepared.target.database}\nReports: {outcome.folder.resolve()}\n\n{comparisons}\n\n"
            + "\n".join(r.error_message for r in runs if r.error_message)
            + "\nComparison mode leaves the last method's copy in the target database."
        )

    def _pick_workload(self):
        path = filedialog.askopenfilename(
            parent=self.root, filetypes=[("Application SQL", "*.sql")]
        )
        if path:
            self.workload.set(path)

    def _cancel(self):
        if self.busy:
            self.cancellation.set()
            self.cancel_button.configure(state="disabled")
            self.status.set(
                "Stopping safely... the current statement may take until its timeout to finish."
            )

    def _history(self):
        recover_jobs(Path("results"))
        dialog = tk.Toplevel(self.root)
        dialog.title("Local job history")
        tree = ttk.Treeview(
            dialog, columns=("state", "phase", "folder"), show="headings", height=15
        )
        for column in ("state", "phase", "folder"):
            tree.heading(column, text=column.capitalize())
        tree.column("folder", width=500)
        tree.pack(fill="both", expand=True, padx=12, pady=12)
        for path, record in owned_jobs(Path("results")):
            tree.insert(
                "", "end", values=(record["state"], record["phase"], str(path.parent.resolve()))
            )
        ttk.Label(
            dialog,
            text="Interrupted jobs must be restarted from their original input; partial targets are not accepted.",
        ).pack(padx=12, pady=8)

        def open_job():
            selected = tree.selection()
            if selected:
                import typer

                typer.launch(tree.item(selected[0], "values")[2])

        ttk.Button(dialog, text="Open selected job reports", command=open_job).pack(pady=8)

        def reopen():
            selected = tree.selection()
            if not selected:
                return
            try:
                folder = Path(tree.item(selected[0], "values")[2])
                record = read_job(folder / "job.json")
                request = CopyRequest(**record["request"])
                request.validate()
                self.kind.set(next(label for label, kind in KINDS.items() if kind == request.kind))
                self._change_kind()
                self.source.set(request.source)
                self.slider.set(request.budget)
                self.method.set(request.method)
                self.seed.set(str(request.seed))
                self.compare.set(request.compare)
                self.workload.set(request.workload or "")
                self.min_survival.set(str(request.min_query_survival * 100))
                self.min_retention.set(str(request.min_result_retention * 100))
                self.required.set(",".join(request.required_queries))
                dialog.destroy()
                self.notebook.select(self.create_page)
                self.status.set(
                    "Choices restored. Click Create to restart from the current original input; a fresh target will be used."
                )
            except (ValueError, TypeError, KeyError, OSError) as exc:
                messagebox.showinfo("Could not restore choices", safe_error(exc), parent=dialog)

        ttk.Button(dialog, text="Restore choices for a fresh restart", command=reopen).pack(pady=8)

    def _connections(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("MySQL connections")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Used for this session. File imports and copies use the target server.",
            wraplength=530,
        ).grid(row=0, column=0, columnspan=3, pady=(0, 12))
        fields = []
        for row, (label, config) in enumerate(
            (("Source server", self.config.source_db), ("Target server", self.config.target_db))
        ):
            variables = {}
            ttk.Label(frame, text=label, style="Step.TLabel").grid(
                row=1 + row * 5, column=0, columnspan=2, sticky="w", pady=8
            )
            for index, (key, value) in enumerate(
                (
                    ("host", config.host),
                    ("port", config.port),
                    ("user", config.user),
                    ("password", config.password),
                )
            ):
                var = tk.StringVar(value=str(value))
                variables[key] = var
                ttk.Label(frame, text=key.capitalize()).grid(
                    row=2 + row * 5 + index, column=0, sticky="w"
                )
                ttk.Entry(
                    frame, textvariable=var, show="•" if key == "password" else "", width=43
                ).grid(row=2 + row * 5 + index, column=1, pady=3)
            fields.append(variables)

        def save():
            try:
                configs = []
                for original, values in zip((self.config.source_db, self.config.target_db), fields):
                    port = int(values["port"].get())
                    if (
                        not 1 <= port <= 65535
                        or not values["host"].get().strip()
                        or not values["user"].get().strip()
                    ):
                        raise ValueError("Enter a host, username, and port from 1 to 65535.")
                    configs.append(
                        replace(
                            original,
                            host=values["host"].get().strip(),
                            port=port,
                            user=values["user"].get().strip(),
                            password=values["password"].get(),
                        )
                    )
                self.config = replace(self.config, source_db=configs[0], target_db=configs[1])
                self._source_changed()
                self.source.set("")
                self.source_box.configure(values=[])
                dialog.destroy()
                self.notebook.select(self.create_page)
                self.status.set("Connection settings updated for this session.")
            except ValueError as exc:
                messagebox.showinfo("Check connection settings", str(exc), parent=dialog)

        ttk.Button(frame, text="Use these settings", command=save).grid(
            row=12, column=0, columnspan=2, pady=15
        )

    def _verify_copy(self):
        if self.outcome is not None and not self.busy:
            outcome = self.outcome
            self._start(lambda: ("evidence", collect_evidence(outcome)))
            self.status.set("Comparing every copied record and checking its connections...")

    def _open(self, kind):
        if self.outcome is not None:
            if kind == "charts":
                show_charts(self.root, self.outcome.folder)
                return
            import typer

            try:
                result = typer.launch(str(self.outcome.folder.resolve()))
                if result:
                    raise OSError("The system could not open the reports folder.")
            except OSError as exc:
                messagebox.showerror("Could not open reports", str(exc), parent=self.root)

    def _close(self):
        if self.busy:
            messagebox.showinfo(
                "Still working",
                "Please wait for the current action to finish before closing.",
                parent=self.root,
            )
            return
        self.root.destroy()


def main():
    # pythonw has no console streams; keep diagnostics in a local log instead.
    if sys.stdout is None or sys.stderr is None:
        sink = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    root = tk.Tk()
    try:
        config = load_config(validate=False)
    except ValueError:
        config = AppConfig(
            DatabaseConfig("localhost", 3306, "", "", ""),
            DatabaseConfig("localhost", 3306, "", "", ""),
        )
    from safeslicesql.logging_config import setup_logging

    setup_logging(level="WARNING", log_dir=config.log_dir, force=True)
    Desktop(root, config)
    root.mainloop()


if __name__ == "__main__":
    main()
