"""
safeslicesql.cli — Command-line interface for SafeSliceSQL.

Provides the `safeslice` CLI with commands for schema inspection,
subset generation, validation, and benchmarking.

Usage:
    safeslice inspect --database employees
    safeslice run --method random-repair --budget-percent 20 --seed 42
    safeslice run --method referential --budget-percent 20 --seed 42
    safeslice run --method workload-aware --budget-percent 20 --seed 42
    safeslice validate --source employees --target employees_slice
    safeslice benchmark --config benchmarks/benchmarks.yaml

"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import typer

from safeslicesql.config import load_config, load_logging_settings
from safeslicesql.db import (
    create_engine,
    get_source_engine,
    get_target_engine,
    test_connection,
)
from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.logging_config import setup_logging
from safeslicesql.pipeline import run_pipeline
from safeslicesql.reporting import (
    print_generation_summary,
    print_selection_summary,
    print_validation_summary,
    print_workload_summary,
    save_run_results_csv,
)
from safeslicesql.sampler import select_rows
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.source_snapshot import capture_source_snapshot
from safeslicesql.validator import evaluate_workload, validate_subset
from safeslicesql.workload_analyzer import analyze_workload, require_usable_workload

logger = logging.getLogger("safeslicesql.cli")

app = typer.Typer(
    name="safeslice",
    help="SafeSliceSQL — Workload-aware database subsetting with "
    "referential integrity preservation.",
    add_completion=False,
)


def _init(log_level: str | None = None) -> None:
    """Apply environment logging defaults and explicit CLI precedence."""
    env_level, log_dir = load_logging_settings()
    setup_logging(level=log_level or env_level, log_dir=log_dir, force=True)


@app.command()
def inspect(
    database: str = typer.Option(
        ...,
        "--database",
        "-d",
        help="Source database name to inspect.",
    ),
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        "-l",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    ),
) -> None:
    """Inspect source database schema and foreign-key dependencies."""
    _init(log_level)
    logger.info("Inspecting database: %s", database)

    try:
        config = load_config(validate=False, require_target=False)
    except Exception as exc:
        typer.echo(f"[error] Failed to load configuration: {exc}", err=True)
        raise typer.Exit(code=1)

    config.source_db.database = database
    errors = config.validate(require_target=False)
    if errors:
        typer.echo("[error] " + "; ".join(errors), err=True)
        raise typer.Exit(code=1)
    engine = create_engine(config.source_db)

    if not test_connection(engine):
        typer.echo(
            f"[error] Could not connect to database '{database}' on "
            f"{config.source_db.host}:{config.source_db.port}. "
            "Please check MySQL service and .env credentials.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        schema = inspect_schema(engine)
        graph = build_dependency_graph(schema)
    except Exception as exc:
        typer.echo(f"[error] Schema inspection failed: {exc}", err=True)
        raise typer.Exit(code=1)

    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print(
        f"\n[bold green]Database Inspection Report:[/bold green] [bold cyan]{database}[/bold cyan]"
    )
    console.print(
        f"Total Tables: [bold]{len(schema.tables)}[/bold] | Total Rows (approx): [bold]{schema.total_row_count:,}[/bold]\n"
    )

    table = Table(
        title="Tables & Foreign Key Dependencies", show_header=True, header_style="bold magenta"
    )
    table.add_column("Table Name", style="cyan", no_wrap=True)
    table.add_column("Primary Key", style="green")
    table.add_column("Rows", justify="right")
    table.add_column("Outgoing Foreign Keys (Child -> Parent)", style="yellow")

    for tbl_name, tbl in schema.tables.items():
        fks_str = (
            ", ".join(
                f"{fk.column} -> {fk.referenced_table}.{fk.referenced_column}"
                for fk in tbl.foreign_keys
            )
            or "[dim]None[/dim]"
        )
        pk_columns = tbl.effective_primary_keys
        pk_str = ", ".join(pk_columns) if pk_columns else "[red]None (unsupported)[/red]"
        table.add_row(tbl_name, pk_str, f"{tbl.row_count:,}", fks_str)

    console.print(table)

    # Topological order / Cycles
    if graph.has_cycles:
        console.print(
            "\n[bold red]Dependency Graph Status: CYCLES DETECTED (Unsupported in v1)[/bold red]"
        )
        for cycle in graph.cycles():
            console.print(f"  [red]Cycle:[/red] {' -> '.join(cycle)}")
    else:
        order = graph.topological_order()
        console.print("\n[bold green]Safe Insertion Order (Parents -> Children):[/bold green]")
        console.print(f"  {' -> '.join(order)}")

    # Unsupported features
    if schema.unsupported_features:
        console.print(
            f"\n[bold yellow]Unsupported Schema Features Detected ({len(schema.unsupported_features)}):[/bold yellow]"
        )
        for feat in schema.unsupported_features:
            console.print(f"  [yellow]- {feat}[/yellow]")
    else:
        console.print("\n[bold green]No unsupported schema features detected.[/bold green]")
    console.print("")


@app.command()
def run(
    method: str = typer.Option(
        ...,
        "--method",
        "-m",
        help="Selection method: random-repair, referential, or workload-aware.",
    ),
    budget_percent: int = typer.Option(
        ...,
        "--budget-percent",
        "-b",
        help="Row budget as a percentage of total source rows (e.g., 20).",
    ),
    seed: int = typer.Option(
        42,
        "--seed",
        "-s",
        help="Random seed for reproducibility.",
    ),
    workload: str = typer.Option(
        "workloads/workload.sql",
        "--workload",
        "-w",
        help="Path to the SQL workload file, or 'auto' to build queries from the source schema.",
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        help="Override source database name in configuration.",
    ),
    target: Optional[str] = typer.Option(
        None,
        "--target",
        help="Override target database name in configuration.",
    ),
    output_dir: str = typer.Option(
        "results",
        "--output-dir",
        "-o",
        help="Directory to save CSV run results.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate selection without materializing into target database.",
    ),
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        "-l",
        help="Logging level.",
    ),
) -> None:
    """Run subset selection and materialization with the specified method."""
    _init(log_level)

    valid_methods = ("random-repair", "referential", "workload-aware")
    if method not in valid_methods:
        typer.echo(
            f"[error] Unknown method '{method}'. Valid methods: {', '.join(valid_methods)}",
            err=True,
        )
        raise typer.Exit(code=2)

    if not (1 <= budget_percent <= 100):
        typer.echo(
            f"[error] budget-percent must be between 1 and 100, got {budget_percent}.",
            err=True,
        )
        raise typer.Exit(code=2)

    logger.info(
        "Starting SafeSlice run: method=%s, budget=%d%%, seed=%d, workload=%s",
        method,
        budget_percent,
        seed,
        workload,
    )

    try:
        config = load_config(validate=False, require_target=not dry_run)
    except Exception as exc:
        typer.echo(f"[error] Failed to load configuration: {exc}", err=True)
        raise typer.Exit(code=1)

    if source:
        config.source_db.database = source
    if target:
        config.target_db.database = target

    config_errors = config.validate(require_target=not dry_run)
    if config_errors:
        for err in config_errors:
            typer.echo(f"[error] Configuration error: {err}", err=True)
        raise typer.Exit(code=1)

    source_engine = get_source_engine(config)
    target_engine = None if dry_run else get_target_engine(config)

    try:
        if not test_connection(source_engine):
            typer.echo(
                f"[error] Could not connect to source database '{config.source_db.database}' on "
                f"{config.source_db.host}:{config.source_db.port}.",
                err=True,
            )
            raise typer.Exit(code=1)

        if not dry_run and not test_connection(target_engine):
            typer.echo(
                f"[error] Could not connect to target database '{config.target_db.database}' on "
                f"{config.target_db.host}:{config.target_db.port}.",
                err=True,
            )
            raise typer.Exit(code=1)

        # 1. Inspect schema & build dependency graph
        try:
            schema = inspect_schema(source_engine)
            graph = build_dependency_graph(schema)
        except Exception as exc:
            typer.echo(f"[error] Schema inspection failed: {exc}", err=True)
            raise typer.Exit(code=1)

        if graph.has_cycles:
            typer.echo(
                "[error] Cyclic foreign-key dependencies detected. Unsupported in v1.", err=True
            )
            raise typer.Exit(code=1)

        # 2. Compute budget
        total_source_rows = schema.total_row_count
        budget = max(1, int(total_source_rows * (budget_percent / 100.0)))
        logger.info(
            "Total source rows: %d, Target budget: %d rows (%d%%)",
            total_source_rows,
            budget,
            budget_percent,
        )

        initial_source_snapshot = capture_source_snapshot(source_engine)

        # 3. Workload Analysis
        workload_path = Path(workload)
        if workload == "auto":
            from safeslicesql.automatic_workload import save_automatic_workload

            try:
                workload_path = save_automatic_workload(schema, Path(output_dir) / "workloads")
                typer.echo(
                    f"[workload] Generated structural demonstration queries: {workload_path}"
                )
            except ValueError as exc:
                typer.echo(f"[error] Cannot generate workload: {exc}", err=True)
                raise typer.Exit(1) from exc
        if workload == "workloads/workload.sql" and config.source_db.database:
            specific_workload = Path(f"workloads/{config.source_db.database}_workload.sql")
            if specific_workload.exists():
                workload_path = specific_workload
                logger.info("Using source-specific workload file: %s", specific_workload)

        try:
            workload_analysis = analyze_workload(workload_path, engine=source_engine)
            require_usable_workload(workload_analysis, for_selection=method == "workload-aware")
            logger.info(
                "Parsed workload with %d queries (%d supported)",
                len(workload_analysis.queries),
                len(workload_analysis.supported_queries),
            )
        except Exception as exc:
            typer.echo(f"[error] Workload analysis failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        if dry_run:
            try:
                selection = select_rows(
                    schema=schema,
                    workload=workload_analysis,
                    graph=graph,
                    engine=source_engine,
                    budget=budget,
                    method=method,
                    seed=seed,
                )
            except Exception as exc:
                typer.echo(f"[error] Row selection failed: {exc}", err=True)
                raise typer.Exit(1) from exc
            print_selection_summary(selection)
            typer.echo(
                "[dry-run] Dry-run requested. Materialization and target validation skipped."
            )
            return

        printers = {
            "selection": print_selection_summary,
            "generation": print_generation_summary,
            "workload": print_workload_summary,
            "validation": print_validation_summary,
        }
        run_result = run_pipeline(
            source_engine=source_engine,
            target_engine=target_engine,
            schema=schema,
            graph=graph,
            workload=workload_analysis,
            budget=budget,
            method=method,
            seed=seed,
            run_id=f"{method}_{budget_percent}pct_s{seed}_{time.time_ns()}",
            budget_percent=float(budget_percent),
            target_database=config.target_db.database,
            initial_source_snapshot=initial_source_snapshot,
            on_stage=lambda name, value: printers[name](value),
        )
        csv_path = save_run_results_csv(run_result, output_dir=output_dir)
        typer.echo(f"[run] Results exported to {csv_path}")
        if not run_result.success:
            typer.echo(f"[error] Run failed: {run_result.error_message}", err=True)
            raise typer.Exit(1)

        typer.echo("[run] SafeSlice completed successfully.")
    finally:
        source_engine.dispose()
        if target_engine is not None:
            target_engine.dispose()


@app.command()
def validate(
    source: str = typer.Option(
        ...,
        "--source",
        help="Source database name.",
    ),
    target: str = typer.Option(
        ...,
        "--target",
        help="Target (subset) database name.",
    ),
    budget_limit: Optional[int] = typer.Option(
        None,
        "--budget-limit",
        help="Optional maximum row count limit.",
    ),
    workload: Optional[str] = typer.Option(
        None,
        "--workload",
        "-w",
        help="Optional workload file path to evaluate retention metrics.",
    ),
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        "-l",
        help="Logging level.",
    ),
) -> None:
    """Validate a generated subset for referential integrity and budget compliance."""
    _init(log_level)
    logger.info("Validating: source=%s, target=%s", source, target)

    try:
        config = load_config(validate=False)
    except Exception as exc:
        typer.echo(f"[error] Failed to load configuration: {exc}", err=True)
        raise typer.Exit(code=1)

    config.source_db.database = source
    config.target_db.database = target

    config_errors = config.validate()
    if config_errors:
        for err in config_errors:
            typer.echo(f"[error] Configuration error: {err}", err=True)
        raise typer.Exit(code=1)

    source_engine = get_source_engine(config)
    target_engine = get_target_engine(config)

    try:
        if not test_connection(source_engine):
            typer.echo(f"[error] Could not connect to source database '{source}'", err=True)
            raise typer.Exit(code=1)

        if not test_connection(target_engine):
            typer.echo(f"[error] Could not connect to target database '{target}'", err=True)
            raise typer.Exit(code=1)

        if budget_limit is None:
            try:
                schema = inspect_schema(source_engine)
                budget_limit = schema.total_row_count
            except Exception as exc:
                typer.echo(f"[error] Cannot determine validation budget: {exc}", err=True)
                raise typer.Exit(code=1) from exc

        report = validate_subset(source_engine, target_engine, budget_limit=budget_limit)
        print_validation_summary(report)

        if workload:
            try:
                wl = analyze_workload(Path(workload), engine=source_engine)
                require_usable_workload(wl)
                wl_report = evaluate_workload(source_engine, target_engine, wl)
                print_workload_summary(wl_report)
            except Exception as exc:
                typer.echo(f"[error] Workload evaluation failed: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            if wl_report.failed_queries:
                raise typer.Exit(code=1)

        if not report.is_valid:
            raise typer.Exit(code=1)
    finally:
        source_engine.dispose()
        target_engine.dispose()


@app.command()
def benchmark(
    config: str = typer.Option(
        "benchmarks/benchmarks.yaml",
        "--config",
        "-c",
        help="Path to the benchmark YAML configuration file.",
    ),
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        "-l",
        help="Logging level.",
    ),
) -> None:
    """Run the full benchmark matrix from a YAML configuration."""
    _init(log_level)
    logger.info("Running benchmark with config: %s", config)

    try:
        from safeslicesql.benchmark import run_benchmark

        results = run_benchmark(config)
    except Exception as exc:
        typer.echo(f"[error] Benchmark failed: {exc}", err=True)
        raise typer.Exit(code=1)

    if results.failed_runs or not results.total_runs:
        typer.echo(
            f"[error] Benchmark completed with {results.failed_runs} failed runs out of {results.total_runs}.",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command()
def selftest(
    cases: int = typer.Option(5, min=1, max=100, help="Number of generated database schemas."),
    rows: int = typer.Option(100, min=1, help="Maximum generated rows per table."),
    seed: int = typer.Option(42, help="Seed for repeatable data and sampling."),
    mysql: bool = typer.Option(
        False, "--mysql", help="Use disposable MySQL schemas with target credentials from .env."
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", help="New directory for test reports and generated data."
    ),
    log_level: Optional[str] = typer.Option("WARNING", "--log-level"),
) -> None:
    """Test varied generated databases, all methods, and reproducibility."""
    from uuid import uuid4

    from safeslicesql.synthetic import run_synthetic_suite

    _init(log_level)
    output = Path(output_dir) if output_dir else Path("results/selftest") / uuid4().hex[:12]
    typer.echo(
        f"Generated-database checks: {'MySQL' if mysql else 'SQLite'}; {cases} cases; up to {rows} rows/table."
    )

    def progress(record):
        status = "PASS" if record["success"] else "FAIL"
        typer.echo(
            f"{status} | case {record['case']} {record['shape']} | {record['method']} | budget {record['budget_percent']}%"
        )
        if record["error"]:
            typer.echo(record["error"], err=True)

    try:
        records = run_synthetic_suite(
            output, cases=cases, rows=rows, seed=seed, mysql=mysql, progress=progress
        )
    except Exception as exc:
        typer.echo(f"[error] Generated-database checks failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    failed = sum(not record["success"] for record in records)
    typer.echo(
        f"Checks: {len(records) - failed} passed, {failed} failed. Reports: {output.resolve()}"
    )
    if failed:
        raise typer.Exit(1)


@app.command()
def desktop() -> None:
    """Open the guided desktop application."""
    from safeslicesql.desktop import main

    main()


@app.command()
def demo() -> None:
    """Open the interactive terminal menu for a live presentation."""
    from safeslicesql.demo import interactive_demo

    interactive_demo()


if __name__ == "__main__":
    app()
