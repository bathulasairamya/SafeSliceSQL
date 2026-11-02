"""A plain-language desktop flow for creating smaller database copies."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4

from safeslicesql.config import AppConfig, DatabaseConfig, load_config
from safeslicesql.desktop_service import CopyRequest, create_copy
from safeslicesql.input_picker import available_databases
from safeslicesql.sampler import METHODS

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
        self.outcome = None
        self.kind = tk.StringVar(value="CSV file")
        self.source = tk.StringVar()
        self.budget = tk.IntVar(value=10)
        self.method = tk.StringVar(value="workload-aware")
        self.seed = tk.StringVar(value="42")
        self.compare = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Choose your data to get started.")
        self.summary = tk.StringVar(value="Your smaller copy and checks will appear here.")
        self.details = tk.StringVar()
        self.advanced_visible = False
        root.title("SafeSliceSQL — Create a smaller data copy")
        root.geometry("1000x820")
        root.minsize(900, 760)
        root.configure(bg="#f4f6fa")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f6fa")
        style.configure("TLabel", background="#f4f6fa", foreground="#18243c", font=("Segoe UI", 11))
        style.configure("Title.TLabel", font=("Segoe UI", 25, "bold"))
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
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)

    def _build(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, background="#f4f6fa", highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        page = ttk.Frame(self.canvas, padding=28)
        window = self.canvas.create_window((0, 0), window=page, anchor="nw")
        page.bind(
            "<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width)
        )
        ttk.Label(page, text="Make big data easier to work with", style="Title.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            page,
            text="Create a smaller, separate copy for testing, training, or a demonstration.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 18))
        ttk.Label(page, text="1   Choose your data", style="Step.TLabel").pack(anchor="w")
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(10, 6))
        self.kind_box = ttk.Combobox(
            row, textvariable=self.kind, values=list(KINDS), state="readonly", width=21
        )
        self.kind_box.pack(side="left", padx=(0, 10))
        self.kind_box.bind("<<ComboboxSelected>>", lambda _: self._change_kind())
        self.source_box = ttk.Combobox(row, textvariable=self.source, state="disabled")
        self.source_box.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.browse = ttk.Button(row, text="Choose file…", command=self._browse)
        self.browse.pack(side="left")
        self.source_note = ttk.Label(
            page,
            text="CSV and Excel files need column names in their first row. Each file or sheet becomes a table.",
            style="Muted.TLabel",
        )
        self.source_note.pack(anchor="w")
        ttk.Label(page, text="2   How much data should the copy keep?", style="Step.TLabel").pack(
            anchor="w", pady=(20, 4)
        )
        controls = ttk.Frame(page)
        controls.pack(fill="x")
        self.slider = ttk.Scale(controls, from_=1, to=100, command=self._slide)
        self.slider.set(10)
        self.slider.pack(side="left", fill="x", expand=True, padx=(0, 16))
        self.percentage = ttk.Label(controls, text="10%", font=("Segoe UI", 19, "bold"), width=6)
        self.percentage.pack(side="left")
        ttk.Label(
            page,
            text="This limits total rows across all tables. The copy may be smaller to keep required relationships valid.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 8))
        self.advanced_toggle = ttk.Button(
            page, text="Advanced options ▸", command=self._toggle_advanced
        )
        self.advanced_toggle.pack(anchor="w")
        self.advanced = ttk.Frame(page)
        ttk.Label(self.advanced, text="Selection method").grid(row=0, column=0, padx=(0, 8))
        self.method_box = ttk.Combobox(
            self.advanced,
            textvariable=self.method,
            values=list(METHODS),
            state="readonly",
            width=19,
        )
        self.method_box.grid(row=0, column=1, padx=(0, 15))
        ttk.Label(self.advanced, text="Repeatable seed").grid(row=0, column=2)
        self.seed_box = ttk.Entry(self.advanced, textvariable=self.seed, width=9)
        self.seed_box.grid(row=0, column=3, padx=8)
        self.compare_box = ttk.Checkbutton(
            self.advanced, text="Compare all three methods", variable=self.compare
        )
        self.compare_box.grid(row=1, column=0, columnspan=3, sticky="w", pady=7)
        self.connection_button = ttk.Button(
            self.advanced, text="Connection settings…", command=self._connections
        )
        self.connection_button.grid(row=1, column=3, columnspan=2)
        self.action_row = ttk.Frame(page)
        self.action_row.pack(fill="x", pady=(16, 8))
        self.run_button = ttk.Button(
            self.action_row, text="Create smaller copy", style="Primary.TButton", command=self._run
        )
        self.run_button.pack(side="left")
        ttk.Label(
            self.action_row,
            text="Your original files or database are kept intact.",
            style="Muted.TLabel",
        ).pack(side="left", padx=16)
        self.progress = ttk.Progressbar(page, mode="indeterminate")
        self.progress.pack(fill="x", pady=5)
        ttk.Label(page, textvariable=self.status, wraplength=900).pack(anchor="w", pady=(2, 12))
        ttk.Separator(page).pack(fill="x")
        ttk.Label(page, text="3   Your result", style="Step.TLabel").pack(anchor="w", pady=(14, 5))
        ttk.Label(
            page, textvariable=self.summary, font=("Segoe UI", 12, "bold"), wraplength=900
        ).pack(anchor="w")
        self.results = ttk.Treeview(page, columns=("check", "result"), show="headings", height=5)
        self.results.heading("check", text="What we checked")
        self.results.heading("result", text="Result")
        self.results.column("check", width=330)
        self.results.column("result", width=470)
        self.results.pack(fill="both", expand=True, pady=10)
        buttons = ttk.Frame(page)
        buttons.pack(fill="x")
        self.report_button = ttk.Button(
            buttons,
            text="Open results folder",
            state="disabled",
            command=lambda: self._open("reports"),
        )
        self.report_button.pack(side="left", padx=(0, 8))
        self.chart_button = ttk.Button(
            buttons, text="View charts", state="disabled", command=lambda: self._open("charts")
        )
        self.chart_button.pack(side="left", padx=(0, 8))
        self.detail_button = ttk.Button(
            buttons,
            text="Details",
            command=lambda: messagebox.showinfo(
                "Details", self.details.get() or "No results yet.", parent=self.root
            ),
        )
        self.detail_button.pack(side="left")
        ttk.Label(
            page,
            text="CSV and Excel values are kept as text. Links between files are not guessed. Your smaller copy is stored separately in MySQL.",
            style="Muted.TLabel",
            wraplength=920,
        ).pack(anchor="w", pady=(12, 0))

    def _slide(self, value):
        self.budget.set(round(float(value)))
        if hasattr(self, "percentage"):
            self.percentage.configure(text=f"{self.budget.get()}%")

    def _toggle_advanced(self):
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced.pack(fill="x", before=self.action_row, pady=8)
        else:
            self.advanced.pack_forget()
        self.advanced_toggle.configure(
            text="Advanced options ▾" if self.advanced_visible else "Advanced options ▸"
        )

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
        try:
            request = CopyRequest(
                KINDS[self.kind.get()],
                self.source.get(),
                self.budget.get(),
                self.method.get(),
                int(self.seed.get()),
                self.compare.get(),
            )
            request.validate()
        except ValueError as exc:
            messagebox.showinfo("Check your choices", str(exc), parent=self.root)
            return
        self.outcome = None
        self.report_button.configure(state="disabled")
        self.chart_button.configure(state="disabled")
        self.results.delete(*self.results.get_children())
        self.summary.set("Creating your smaller copy…")
        self.details.set("")
        output = Path("results/desktop") / uuid4().hex[:12]
        self._start(
            lambda: (
                "copy",
                create_copy(
                    self.config, request, output, lambda text: self.events.put(("status", text))
                ),
            )
        )

    def _start(self, task):
        if self.busy:
            return
        self.busy = True
        self._lock(True)
        self.progress.start(12)
        self.status.set("Working…")

        def worker():
            try:
                self.events.put(task())
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _lock(self, locked):
        for widget in (
            self.browse,
            self.slider,
            self.advanced_toggle,
            self.run_button,
            self.seed_box,
            self.compare_box,
            self.connection_button,
        ):
            widget.configure(state="disabled" if locked else "normal")
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
                elif kind == "copy":
                    self._show_outcome(value)
                else:
                    self.status.set(
                        "Could not complete this action. Open Details for the reason, or check Connection settings."
                    )
                    self.summary.set("No completed copy to show.")
                    self.details.set(value)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _show_outcome(self, outcome):
        self.outcome = outcome
        runs = outcome.results.runs
        if not runs:
            self.status.set("No copy was produced. Open the results folder for details.")
            return
        run = runs[-1]
        validation = run.validation
        original = sum(outcome.prepared.tables.values())
        actual = validation.actual_rows if validation else 0
        passed = outcome.results.failed_runs == 0 and run.success
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
        for check in checks:
            self.results.insert("", "end", values=check)
        self.status.set(
            "Completed. Your separate copy is stored in MySQL."
            if passed
            else "Some checks failed. See Details and reports before using this copy."
        )
        self.report_button.configure(state="normal")
        self.chart_button.configure(
            state="normal" if (outcome.folder / "plots").is_dir() else "disabled"
        )
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
                self.source.set("")
                self.source_box.configure(values=[])
                dialog.destroy()
                self.status.set("Connection settings updated for this session.")
            except ValueError as exc:
                messagebox.showinfo("Check connection settings", str(exc), parent=dialog)

        ttk.Button(frame, text="Use these settings", command=save).grid(
            row=12, column=0, columnspan=2, pady=15
        )

    def _open(self, kind):
        if self.outcome is not None:
            import typer

            folder = self.outcome.folder / "plots" if kind == "charts" else self.outcome.folder
            typer.launch(str(folder.resolve()))

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
        Path("logs").mkdir(exist_ok=True)
        log = open("logs/desktop.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    root = tk.Tk()
    try:
        config = load_config(validate=False)
    except ValueError:
        config = AppConfig(
            DatabaseConfig("localhost", 3306, "", "", ""),
            DatabaseConfig("localhost", 3306, "", "", ""),
        )
    Desktop(root, config)
    root.mainloop()


if __name__ == "__main__":
    main()
