"""Interactive presentation menu over the existing CLI pipelines."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from safeslicesql.config import DatabaseConfig, load_config
from safeslicesql.sampler import METHODS

console = Console()


def _choice(label: str, choices: list[str], default: str | None = None) -> str:
    while True:
        value = typer.prompt(label, default=default, type=str)
        if value in choices:
            return value
        console.print("Choose one of: " + ", ".join(choices), style="red")


@dataclass
class DemoSettings:
    source: str
    target: str
    workload: str
    method: str = "workload-aware"
    budget: int = 10
    seed: int = 42
    source_connection: DatabaseConfig | None = None


def _execute(arguments: list[str], settings: DemoSettings) -> bool:
    # Arguments stay separate: database names and paths are never shell commands.
    environment = dict(os.environ)
    environment["SAFESLICE_SOURCE_DATABASE"] = settings.source
    environment["SAFESLICE_TARGET_DATABASE"] = settings.target
    if settings.source_connection is not None:
        connection = settings.source_connection
        for key, value in (
            ("HOST", connection.host),
            ("PORT", str(connection.port)),
            ("USER", connection.user),
            ("PASSWORD", connection.password),
        ):
            environment[f"SAFESLICE_SOURCE_{key}"] = value
    try:
        result = subprocess.run(
            [sys.executable, "-m", "safeslicesql.cli", *arguments, "--log-level", "WARNING"],
            env=environment,
            check=False,
        )
    except OSError as exc:
        console.print(f"Could not start demo action: {exc}", style="red", markup=False)
        return False
    if result.returncode:
        console.print(
            "Action failed. Review the error above; you can change settings and retry.", style="red"
        )
        return False
    return True


def _change_settings(settings: DemoSettings) -> None:
    console.print("[bold]Change settings[/bold] — press Enter to keep a value.")
    for index, method in enumerate(METHODS, start=1):
        console.print(f"  {index}. {method}")
    method = _choice("Method", ["1", "2", "3"], str(METHODS.index(settings.method) + 1))
    settings.method = METHODS[int(method) - 1]
    while True:
        budget = typer.prompt("Row budget (%)", default=settings.budget, type=int)
        if 1 <= budget <= 100:
            settings.budget = budget
            break
        console.print("Budget must be between 1 and 100.", style="red")
    settings.seed = typer.prompt("Random seed", default=settings.seed, type=int)
    previous_source = settings.source
    settings.source = typer.prompt("Source database", default=settings.source).strip()
    if settings.source != previous_source:
        settings.source_connection = None
        settings.workload = "auto"
    settings.target = typer.prompt("Dedicated target database", default=settings.target).strip()
    settings.workload = typer.prompt(
        "Workload SQL file (or auto for this schema)", default=settings.workload
    ).strip()


def _run_arguments(settings: DemoSettings, output: Path, *, dry_run: bool = False) -> list[str]:
    arguments = [
        "run",
        "--method",
        settings.method,
        "--budget-percent",
        str(settings.budget),
        "--seed",
        str(settings.seed),
        "--source",
        settings.source,
        "--target",
        settings.target,
        "--workload",
        settings.workload,
        "--output-dir",
        str(output),
    ]
    if dry_run:
        arguments.append("--dry-run")
    return arguments


def _faculty_input(settings: DemoSettings) -> None:
    from safeslicesql.dataset_input import prepare_dataset
    from safeslicesql.input_picker import available_databases, pick_input

    console.print(
        "[bold]Load a faculty dataset[/bold]\n1  Existing MySQL database\n"
        "2  CSV file or folder of CSV files\n3  Excel .xlsx workbook"
    )
    kind = _choice("Input format", ["1", "2", "3"])
    database, files = None, None
    try:
        config = load_config(validate=False)
        if kind == "1":
            names = available_databases(config)
            if not names:
                console.print("No user databases are visible with the configured source account.")
                return
            for number, name in enumerate(names, 1):
                console.print(f"{number}  {name}", markup=False)
            selection = _choice(
                "Choose database number (0 cancels)", [str(i) for i in range(len(names) + 1)]
            )
            if selection == "0":
                return
            database = names[int(selection) - 1]
        else:
            picker_kind = "xlsx"
            if kind == "2":
                mode = _choice("1  Single CSV file, 2  Folder of CSV files", ["1", "2"], "1")
                picker_kind = "csv" if mode == "1" else "folder"
            console.print("Select your dataset in the file-selection window.")
            files = pick_input(picker_kind)
            if files is None:
                console.print("Selection cancelled. Active dataset unchanged.")
                return
            console.print(
                "File values are stored as text, with an added row ID. "
                "Each CSV/sheet becomes a table; relationships are not guessed."
            )
        output = Path("results/datasets") / uuid4().hex[:12]
        prepared = prepare_dataset(config, output, database=database, files=files)
    except Exception as exc:
        console.print(f"Dataset could not be prepared: {exc}", style="red", markup=False)
        return
    # Replace active settings only after all preparation steps succeed.
    settings.source = prepared.source.database
    settings.target = prepared.target.database
    settings.source_connection = prepared.source
    settings.workload = str(prepared.workload.resolve())
    table = Table(title="Faculty dataset ready")
    table.add_column("Table")
    table.add_column("Source rows", justify="right")
    for name, count in prepared.tables.items():
        table.add_row(name, str(count))
    console.print(table)
    console.print(f"Separate target created: {settings.target}", markup=False)
    console.print(
        "Structural demonstration queries were generated from the schema. "
        "Use option 2 to supply business queries if provided."
    )
    console.print(f"Dataset mapping: {prepared.manifest.resolve()}", markup=False)
    console.print(
        "[green]Choose 3 to preview, 4 to generate a subset, or 5 to compare methods.[/green]"
    )


def _comparison_config(settings: DemoSettings, output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    path = output / "comparison.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "matrix": {
                    "methods": list(METHODS),
                    "budget_percents": [settings.budget],
                    "seeds": [settings.seed],
                },
                "workload": {
                    "path": "auto"
                    if settings.workload == "auto"
                    else str(Path(settings.workload).resolve())
                },
                "output": {
                    "raw_dir": str((output / "raw").resolve()),
                    "summary_dir": str((output / "summaries").resolve()),
                    "plots_dir": str((output / "plots").resolve()),
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _show_results(output: Path | None) -> None:
    if output is None:
        console.print("Run a subset or comparison first.")
        return
    console.print(f"Results: {output.resolve()}", markup=False)
    check_report = output / "results.csv"
    if check_report.is_file():
        with check_report.open(encoding="utf-8", newline="") as handle:
            table = Table(title="Generated database checks")
            for label in ("Case", "Shape", "Method", "Budget %", "Passed", "Error"):
                table.add_column(label)
            for row in csv.DictReader(handle):
                table.add_row(
                    row["case"],
                    row["shape"],
                    row.get("method", "Setup/cleanup"),
                    row.get("budget_percent", ""),
                    row["success"],
                    row["error"],
                )
            console.print(table)
        return
    files = sorted(output.rglob("run_*.csv"))
    if not files:
        console.print("No run reports were produced. Review the action's error output.")
    for path in files:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            table = Table(title=f"{row['method']} — budget {row['budget_percent']}%")
            table.add_column("Check / metric")
            table.add_column("Value")
            for key in (
                "success",
                "actual_rows",
                "budget_limit",
                "pk_unique",
                "fk_violations",
                "schema_compatible",
                "source_unchanged",
                "failed_queries",
                "query_survival_rate",
            ):
                table.add_row(key.replace("_", " ").capitalize(), row.get(key, "N/A"))
            console.print(table)
            if row.get("error_message"):
                console.print(row["error_message"], style="red", markup=False)
        query_path = path.with_name(path.name.replace("run_", "queries_", 1))
        if query_path.exists():
            with query_path.open(encoding="utf-8", newline="") as handle:
                table = Table(title="Query coverage")
                for column in ("Query", "Survived", "Source rows", "Subset rows", "Status"):
                    table.add_column(column)
                for row in csv.DictReader(handle):
                    table.add_row(
                        row["query_id"],
                        row["survived"],
                        row["source_row_count"],
                        row["target_row_count"],
                        row.get("error") or row.get("exclusion_reason") or "Executed",
                    )
                console.print(table)


def interactive_demo() -> None:
    """Keep controls available between live runs, without requiring a browser."""
    try:
        config = load_config(validate=False)
    except ValueError as exc:
        console.print(str(exc), style="red", markup=False)
        raise typer.Exit(1) from exc
    source = config.source_db.database or "world"
    specific = Path("workloads") / f"{source}_workload.sql"
    settings = DemoSettings(
        source,
        config.target_db.database or f"{source}_demo",
        str(specific if specific.is_file() else Path("workloads/workload.sql")),
    )
    last_output: Path | None = None
    try:
        while True:
            console.print(
                Panel(
                    Text(
                        f"Source: {settings.source}   Target: {settings.target}\n"
                        f"Method: {settings.method}   Budget: {settings.budget}%   Seed: {settings.seed}\n"
                        f"Workload: {settings.workload}"
                    ),
                    title="SafeSliceSQL Live Demo",
                )
            )
            console.print(
                "1  Inspect source database\n2  Change settings\n3  Preview selection (dry run)\n"
                "4  Generate subset and validate\n5  Compare all three methods\n"
                "6  Show latest results\n7  Open latest charts folder\n"
                "8  Test generated databases\n9  Load a faculty dataset\n0  Exit"
            )
            console.print(
                "[dim]Options 4 and 5 refresh the configured target. Use a dedicated, existing test database.[/dim]"
            )
            choice = _choice("Choose", [str(i) for i in range(10)])
            if choice == "0":
                break
            try:
                if choice == "1":
                    _execute(["inspect", "--database", settings.source], settings)
                elif choice == "2":
                    _change_settings(settings)
                elif choice in ("3", "4", "5"):
                    if settings.workload != "auto" and not Path(settings.workload).is_file():
                        console.print(
                            "Workload file does not exist. Choose 2 to correct its path.",
                            style="red",
                        )
                        continue
                    output = Path("results/demo") / uuid4().hex[:12]
                    if choice == "5":
                        path = _comparison_config(settings, output)
                        arguments = ["benchmark", "--config", str(path)]
                    else:
                        arguments = _run_arguments(settings, output, dry_run=choice == "3")
                    success = _execute(arguments, settings)
                    if choice != "3":
                        last_output = output
                        console.print(f"Reports for this action: {output.resolve()}", markup=False)
                    if success:
                        console.print(
                            "[green]Action complete. Choose another option to continue.[/green]"
                        )
                elif choice == "6":
                    _show_results(last_output)
                elif choice == "7":
                    folder = last_output / "plots" if last_output is not None else None
                    if folder is not None and folder.is_dir():
                        typer.launch(str(folder.resolve()), locate=False)
                    else:
                        console.print("Choose 5 to generate comparison charts first.")
                elif choice == "8":
                    console.print(
                        "Tests generated schemas, not the selected source. SQLite needs no MySQL connection."
                    )
                    cases = typer.prompt("Database cases", default=5, type=int)
                    rows = typer.prompt("Maximum rows per table", default=100, type=int)
                    if cases < 1 or cases > 100 or rows < 1:
                        console.print("Use 1-100 cases and a positive row scale.", style="red")
                        continue
                    backend = _choice(
                        "Backend: 1 SQLite, 2 MySQL (creates/deletes disposable test schemas)",
                        ["1", "2"],
                        "1",
                    )
                    arguments = [
                        "selftest",
                        "--cases",
                        str(cases),
                        "--rows",
                        str(rows),
                        "--seed",
                        str(settings.seed),
                    ]
                    last_output = Path("results/selftest") / uuid4().hex[:12]
                    arguments.extend(["--output-dir", str(last_output)])
                    if backend == "2":
                        arguments.append("--mysql")
                    _execute(arguments, settings)
                elif choice == "9":
                    _faculty_input(settings)
            except (OSError, ValueError) as exc:
                console.print(f"Action failed: {exc}", style="red", markup=False)
    except (typer.Abort, KeyboardInterrupt, EOFError):
        pass
    console.print("Demo closed.")
