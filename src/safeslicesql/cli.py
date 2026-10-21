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

Owner: M3
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import typer

from safeslicesql.config import load_config
from safeslicesql.db import (
    create_engine,
    get_source_engine,
    get_target_engine,
    test_connection,
)
from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.logging_config import setup_logging
from safeslicesql.reporting import (
    print_generation_summary,
    print_selection_summary,
    print_validation_summary,
    print_workload_summary,
    save_run_results_csv,
)
from safeslicesql.sampler import select_rows
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.types import BenchmarkRunResult
from safeslicesql.validator import evaluate_workload, validate_subset
from safeslicesql.workload_analyzer import analyze_workload

logger = logging.getLogger("safeslicesql.cli")

app = typer.Typer(
    name="safeslice",
    help="SafeSliceSQL — Workload-aware database subsetting with "
    "referential integrity preservation.",
    add_completion=False,
)


def _init(log_level: str = "INFO") -> None:
    """Initialize logging and configuration."""
    setup_logging(level=log_level)


@app.command()
def inspect(
    database: str = typer.Option(
        ...,
        "--database",
        "-d",
        help="Source database name to inspect.",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        "-l",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    ),
) -> None:
    """Inspect source database schema and foreign-key dependencies."""
    _init(log_level)
    logger.info("Inspecting database: %s", database)

    try:
        config = load_config()
    except Exception as exc:
        typer.echo(f"[error] Failed to load configuration: {exc}", err=True)
        raise typer.Exit(code=1)

    config.source_db.database = database
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
        pk_str = tbl.primary_key or "[red]None (unsupported)[/red]"
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
        help="Path to the SQL workload file.",
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
    log_level: str = typer.Option(
        "INFO",
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
        config = load_config()
    except Exception as exc:
        typer.echo(f"[error] Failed to load configuration: {exc}", err=True)
        raise typer.Exit(code=1)

    if source:
        config.source_db.database = source
    if target:
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
            typer.echo(
                f"[error] Could not connect to source database '{config.source_db.database}' on "
                f"{config.source_db.host}:{config.source_db.port}.",
                err=True,
            )
            raise typer.Exit(code=1)

        if not test_connection(target_engine):
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

        # 3. Workload Analysis
        workload_path = Path(workload)
        workload_analysis = None
        if workload_path.exists():
            try:
                workload_analysis = analyze_workload(workload_path, engine=source_engine)
                logger.info(
                    "Parsed workload with %d queries (%d supported)",
                    len(workload_analysis.queries),
                    len(workload_analysis.supported_queries),
                )
            except Exception as exc:
                logger.warning("Workload analysis failed: %s", exc)

        # 4. Selection
        run_id = f"{method}_{budget_percent}pct_s{seed}_{int(time.time())}"
        t0 = time.time()
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
            raise typer.Exit(code=1)
        runtime_selection = time.time() - t0
        print_selection_summary(selection)

        if dry_run:
            typer.echo(
                "[dry-run] Dry-run requested. Materialization and target validation skipped."
            )
            return

        # 5. Materialization
        t0 = time.time()
        try:
            generation = materialize_subset(
                source_engine=source_engine,
                target_engine=target_engine,
                schema=schema,
                selection=selection,
                graph=graph,
                target_database=config.target_db.database,
            )
        except Exception as exc:
            typer.echo(f"[error] Materialization failed: {exc}", err=True)
            raise typer.Exit(code=1)
        runtime_materialization = time.time() - t0
        print_generation_summary(generation)

        if not generation.success:
            typer.echo(f"[error] Materialization failed: {generation.error_message}", err=True)
            raise typer.Exit(code=1)

        # 6. Validation
        t0 = time.time()
        try:
            validation = validate_subset(
                source_engine=source_engine,
                target_engine=target_engine,
                budget_limit=budget,
                initial_source_row_count=total_source_rows,
            )
        except Exception as exc:
            typer.echo(f"[error] Validation failed: {exc}", err=True)
            raise typer.Exit(code=1)
        runtime_validation = time.time() - t0
        print_validation_summary(validation)

        # 7. Workload Evaluation
        workload_report = None
        runtime_workload = 0.0
        if workload_analysis and len(workload_analysis.supported_queries) > 0:
            t0 = time.time()
            try:
                workload_report = evaluate_workload(
                    source_engine=source_engine,
                    target_engine=target_engine,
                    workload=workload_analysis,
                )
                print_workload_summary(workload_report)
            except Exception as exc:
                logger.warning("Workload evaluation encountered an error: %s", exc)
            runtime_workload = time.time() - t0

        # 8. Save CSV results
        run_result = BenchmarkRunResult(
            run_id=run_id,
            method=method,
            seed=seed,
            budget_percent=float(budget_percent),
            dataset_size=total_source_rows,
            selection=selection,
            generation=generation,
            validation=validation,
            workload=workload_report,
            runtime_selection=runtime_selection,
            runtime_materialization=runtime_materialization,
            runtime_validation=runtime_validation,
            runtime_workload_eval=runtime_workload,
            success=validation.is_valid and generation.success,
            error_message="; ".join(validation.errors) if validation.errors else "",
        )

        csv_path = save_run_results_csv(run_result, output_dir=output_dir)
        typer.echo(f"[run] Results exported to {csv_path}")

        if not validation.is_valid:
            typer.echo("[error] Validation checks failed.", err=True)
            raise typer.Exit(code=1)

        typer.echo("[run] SafeSlice completed successfully.")
    finally:
        source_engine.dispose()
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
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        "-l",
        help="Logging level.",
    ),
) -> None:
    """Validate a generated subset for referential integrity and budget compliance."""
    _init(log_level)
    logger.info("Validating: source=%s, target=%s", source, target)

    try:
        config = load_config()
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
            except Exception:
                budget_limit = 100_000_000

        report = validate_subset(source_engine, target_engine, budget_limit=budget_limit)
        print_validation_summary(report)

        if workload:
            wl_path = Path(workload)
            if wl_path.exists():
                wl = analyze_workload(wl_path, engine=source_engine)
                wl_report = evaluate_workload(source_engine, target_engine, wl)
                print_workload_summary(wl_report)

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
    log_level: str = typer.Option(
        "INFO",
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

        run_benchmark(config)
    except NotImplementedError:
        typer.echo("[benchmark] Phase 8 benchmark runner is being prepared.", err=True)
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(f"[error] Benchmark failed: {exc}", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
