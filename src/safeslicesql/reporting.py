"""
safeslicesql.reporting — Result formatting, CSV export, and logging utilities.

Provides functions to format validation reports, workload reports, and
benchmark results into structured output (CSV, console tables, logs).

Owner: M3
"""

from __future__ import annotations

import csv
import logging
import os
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from safeslicesql.types import (
    BenchmarkRunResult,
    GenerationResult,
    SelectionResult,
    ValidationReport,
    WorkloadReport,
)

logger = logging.getLogger("safeslicesql.reporting")
console = Console()


def save_run_results_csv(
    run_result: BenchmarkRunResult,
    output_dir: str | Path,
) -> Path:
    """
    Save a single benchmark run's results to a CSV file.

    Parameters
    ----------
    run_result : BenchmarkRunResult
        The complete results of a single run.
    output_dir : str or Path
        Directory to write the CSV file.

    Returns
    -------
    Path
        Path to the written CSV file.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    csv_file = out_path / f"run_{run_result.run_id}.csv"

    # Derive metrics from nested objects
    sel = run_result.selection
    gen = run_result.generation
    val = run_result.validation
    wl = run_result.workload

    total_runtime = (
        run_result.runtime_selection
        + run_result.runtime_materialization
        + run_result.runtime_validation
        + run_result.runtime_workload_eval
    )

    row_data: dict[str, Any] = {
        "run_id": run_result.run_id,
        "method": run_result.method,
        "seed": run_result.seed,
        "budget_percent": run_result.budget_percent,
        "dataset_size": run_result.dataset_size,
        "success": run_result.success,
        "total_selected": sel.total_selected if sel else 0,
        "closure_additions": sel.closure_additions if sel else 0,
        "rejected_candidates": sel.rejected_candidates if sel else 0,
        "total_rows_inserted": gen.total_rows_inserted if gen else 0,
        "is_valid": val.is_valid if val else False,
        "pk_unique": val.pk_unique if val else False,
        "fk_violations": val.fk_violations if val else 0,
        "budget_compliant": val.budget_compliant if val else False,
        "actual_rows": val.actual_rows if val else 0,
        "budget_limit": val.budget_limit if val else 0,
        "schema_compatible": val.schema_compatible if val else False,
        "source_unchanged": val.source_unchanged if val else False,
        "total_queries": wl.total_queries if wl else 0,
        "supported_queries": wl.supported_queries if wl else 0,
        "query_survival_rate": wl.query_survival_rate if wl else 0.0,
        "overall_key_retention": wl.overall_key_retention if wl else 0.0,
        "overall_result_retention": wl.overall_result_retention if wl else 0.0,
        "overall_aggregate_retention": wl.overall_aggregate_retention if wl else 0.0,
        "runtime_selection": run_result.runtime_selection,
        "runtime_materialization": run_result.runtime_materialization,
        "runtime_validation": run_result.runtime_validation,
        "runtime_workload_eval": run_result.runtime_workload_eval,
        "total_runtime": total_runtime,
        "error_message": run_result.error_message,
    }

    with open(csv_file, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row_data.keys()))
        writer.writeheader()
        writer.writerow(row_data)

    # If per-query results exist, save queries breakdown CSV
    if wl and wl.per_query_results:
        queries_file = out_path / f"queries_{run_result.run_id}.csv"
        query_fields = [
            "query_id", "survived", "source_row_count", "target_row_count",
            "key_retention", "result_row_retention", "aggregate_retention",
            "execution_time_source", "execution_time_target", "excluded", "exclusion_reason", "error"
        ]
        with open(queries_file, mode="w", newline="", encoding="utf-8") as qf:
            q_writer = csv.DictWriter(qf, fieldnames=query_fields)
            q_writer.writeheader()
            for q in wl.per_query_results:
                q_writer.writerow({
                    "query_id": q.query_id,
                    "survived": q.survived,
                    "source_row_count": q.source_row_count,
                    "target_row_count": q.target_row_count,
                    "key_retention": q.key_retention,
                    "result_row_retention": q.result_row_retention,
                    "aggregate_retention": q.aggregate_retention,
                    "execution_time_source": q.execution_time_source,
                    "execution_time_target": q.execution_time_target,
                    "excluded": q.excluded,
                    "exclusion_reason": q.exclusion_reason,
                    "error": q.error,
                })

    logger.info("Saved run results to %s", csv_file)
    return csv_file


def print_validation_summary(report: ValidationReport) -> None:
    """Print a human-readable validation summary to the console using Rich."""
    status_style = "bold green" if report.is_valid else "bold red"
    status_text = "PASSED" if report.is_valid else "FAILED"

    console.print(f"\n[bold]Subset Validation Summary:[/bold] [{status_style}]{status_text}[/{status_style}]\n")

    table = Table(title="Validation Checks", show_header=True, header_style="bold cyan")
    table.add_column("Check", style="white")
    table.add_column("Result", justify="center")
    table.add_column("Details", style="dim")

    table.add_row(
        "Hard Row Budget",
        "[green]PASS[/green]" if report.budget_compliant else "[red]FAIL[/red]",
        f"{report.actual_rows:,} / {report.budget_limit:,} rows (Actual / Limit)",
    )
    table.add_row(
        "Primary Key Uniqueness",
        "[green]PASS[/green]" if report.pk_unique else "[red]FAIL[/red]",
        "Zero duplicate primary keys found across all tables" if report.pk_unique else "Duplicate PKs detected",
    )
    table.add_row(
        "Foreign Key Integrity",
        "[green]PASS[/green]" if report.fk_violations == 0 else "[red]FAIL[/red]",
        f"{report.fk_violations} orphaned foreign-key reference(s)",
    )
    table.add_row(
        "Schema Compatibility",
        "[green]PASS[/green]" if report.schema_compatible else "[red]FAIL[/red]",
        "All source tables and columns reproduced on target" if report.schema_compatible else f"Missing: {report.tables_missing}",
    )
    table.add_row(
        "Source Immutability",
        "[green]PASS[/green]" if report.source_unchanged else "[red]FAIL[/red]",
        "Source database verified unchanged" if report.source_unchanged else "Source database altered",
    )

    console.print(table)

    if report.fk_violation_details:
        console.print("\n[bold red]Foreign Key Violations Detected:[/bold red]")
        for detail in report.fk_violation_details:
            console.print(f"  [red]- {detail}[/red]")

    if report.errors:
        console.print("\n[bold red]Validation Errors:[/bold red]")
        for err in report.errors:
            console.print(f"  [red]- {err}[/red]")
    console.print("")


def print_workload_summary(report: WorkloadReport) -> None:
    """Print a human-readable workload evaluation summary using Rich."""
    console.print("\n[bold cyan]Workload Evaluation Summary:[/bold cyan]")
    console.print(
        f"Queries: Total=[bold]{report.total_queries}[/bold] | "
        f"Supported=[bold]{report.supported_queries}[/bold] | "
        f"Excluded=[bold]{report.excluded_queries}[/bold] | "
        f"Source Non-Empty=[bold]{report.source_nonempty_queries}[/bold]"
    )
    console.print(
        f"Metrics: Survival Rate=[bold green]{report.query_survival_rate * 100:.1f}%[/bold green] | "
        f"Key Retention=[bold cyan]{report.overall_key_retention * 100:.1f}%[/bold cyan] | "
        f"Row Retention=[bold yellow]{report.overall_result_retention * 100:.1f}%[/bold yellow] | "
        f"Agg Retention=[bold magenta]{report.overall_aggregate_retention * 100:.1f}%[/bold magenta]\n"
    )

    if report.per_query_results:
        table = Table(title="Per-Query Retention Metrics", show_header=True, header_style="bold magenta")
        table.add_column("Query ID", style="cyan", no_wrap=True)
        table.add_column("Survived", justify="center")
        table.add_column("Source Rows", justify="right")
        table.add_column("Target Rows", justify="right")
        table.add_column("Key Ret.", justify="right")
        table.add_column("Row Ret.", justify="right")
        table.add_column("Agg Ret.", justify="right")
        table.add_column("Notes", style="dim")

        for q in report.per_query_results:
            if q.excluded:
                table.add_row(
                    q.query_id,
                    "[dim]EXCLUDED[/dim]",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    f"[yellow]{q.exclusion_reason}[/yellow]",
                )
            elif q.error:
                table.add_row(
                    q.query_id,
                    "[red]ERROR[/red]",
                    str(q.source_row_count),
                    "-",
                    "-",
                    "-",
                    "-",
                    f"[red]{q.error[:40]}[/red]",
                )
            else:
                surv_str = "[green]YES[/green]" if q.survived else "[red]NO[/red]"
                table.add_row(
                    q.query_id,
                    surv_str,
                    f"{q.source_row_count:,}",
                    f"{q.target_row_count:,}",
                    f"{q.key_retention * 100:.1f}%",
                    f"{q.result_row_retention * 100:.1f}%",
                    f"{q.aggregate_retention * 100:.1f}%",
                    "",
                )

        console.print(table)
    console.print("")


def print_selection_summary(result: SelectionResult) -> None:
    """Print a human-readable selection summary."""
    console.print(f"\n[bold green]Selection Complete:[/bold green] Method=[bold]{result.method}[/bold] | Seed=[bold]{result.seed}[/bold]")
    console.print(
        f"Total Selected: [bold]{result.total_selected:,}[/bold] / Budget: [bold]{result.budget:,}[/bold] "
        f"(Closure Additions: {result.closure_additions:,}, Rejected Candidates: {result.rejected_candidates:,})\n"
    )

    table = Table(title="Selected Rows by Table", show_header=True, header_style="bold green")
    table.add_column("Table Name", style="cyan")
    table.add_column("Selected Rows", justify="right")

    for tbl, pks in sorted(result.selected_rows.items()):
        table.add_row(tbl, f"{len(pks):,}")

    console.print(table)
    console.print("")


def print_generation_summary(result: GenerationResult) -> None:
    """Print a human-readable materialization summary."""
    status_color = "green" if result.success else "red"
    status_text = "SUCCESS" if result.success else "FAILED"

    console.print(f"\n[bold]Subset Materialization:[/bold] [{status_color}]{status_text}[/{status_color}]")
    console.print(f"Target Database: [bold cyan]{result.target_database}[/bold cyan] | Total Rows Inserted: [bold]{result.total_rows_inserted:,}[/bold]\n")

    if result.insertion_order:
        table = Table(title="Materialized Tables & Insertion Order", show_header=True, header_style="bold blue")
        table.add_column("Order", justify="center", style="dim")
        table.add_column("Table Name", style="cyan")
        table.add_column("Rows Inserted", justify="right")

        for idx, tbl in enumerate(result.insertion_order, 1):
            rows = result.rows_inserted.get(tbl, 0)
            table.add_row(str(idx), tbl, f"{rows:,}")

        console.print(table)

    if not result.success and result.error_message:
        console.print(f"\n[bold red]Materialization Error:[/bold red] {result.error_message}")
    console.print("")

