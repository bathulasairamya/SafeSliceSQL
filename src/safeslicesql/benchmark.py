"""
safeslicesql.benchmark — Benchmark runner and experiment management.

Executes the benchmark matrix across methods, dataset sizes, budgets,
and seeds. Records all results to structured CSV files for reproducible
analysis and plotting.

Owner: M3
"""

from __future__ import annotations

import csv
import logging
import math
import time
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import yaml
from rich.console import Console
from rich.table import Table
from sqlalchemy import Engine

from safeslicesql.config import load_config
from safeslicesql.db import get_source_engine, get_target_engine, test_connection
from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.reporting import save_run_results_csv
from safeslicesql.sampler import METHODS, select_rows
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.source_snapshot import capture_source_snapshot
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.types import (
    BenchmarkResults,
    BenchmarkRunResult,
    GenerationResult,
    SelectionResult,
    ValidationReport,
    WorkloadReport,
)
from safeslicesql.validator import evaluate_workload, validate_subset
from safeslicesql.workload_analyzer import analyze_workload, require_usable_workload

logger = logging.getLogger("safeslicesql.benchmark")
console = Console()


def load_benchmark_config(config_path: str | Path) -> dict[str, Any]:
    """Load benchmark configuration from a YAML file with sensible defaults."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Benchmark configuration file not found at: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("Benchmark configuration must be a mapping.")
    for section in ("matrix", "workload", "output", "database"):
        if section in data and not isinstance(data[section], dict):
            raise ValueError(f"Benchmark section '{section}' must be a mapping.")

    # Extract sections with defaults
    matrix = data.get("matrix", {})
    methods = matrix.get("methods", ["random-repair", "referential", "workload-aware"])
    budget_percents = matrix.get("budget_percents", [10, 20, 40])
    seeds = matrix.get("seeds", [42, 123, 999])

    for name, values in (
        ("methods", methods),
        ("budget_percents", budget_percents),
        ("seeds", seeds),
    ):
        if not isinstance(values, list) or not values:
            raise ValueError(f"matrix.{name} must be a nonempty list.")
    if any(not isinstance(method, str) or method not in METHODS for method in methods):
        raise ValueError(f"matrix.methods must contain only: {', '.join(METHODS)}")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 < value <= 100
        for value in budget_percents
    ):
        raise ValueError(
            "matrix.budget_percents must contain finite percentages greater than 0 and at most 100."
        )
    if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds):
        raise ValueError("matrix.seeds must contain integers.")

    workload = data.get("workload", {})
    workload_path = workload.get("path", "workloads/workload.sql")

    output = data.get("output", {})
    raw_dir = output.get("raw_dir", "results/raw")
    summary_dir = output.get("summary_dir", "results/summaries")
    plots_dir = output.get("plots_dir", "results/plots")

    for name, value in (
        ("workload.path", workload_path),
        ("output.raw_dir", raw_dir),
        ("output.summary_dir", summary_dir),
        ("output.plots_dir", plots_dir),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty path string.")

    db_cfg = data.get("database", {})
    source_url = db_cfg.get("source_url")
    target_url = db_cfg.get("target_url")
    target_database_name = db_cfg.get("target_database_name")

    return {
        "methods": methods,
        "budget_percents": budget_percents,
        "seeds": seeds,
        "workload_path": workload_path,
        "raw_dir": raw_dir,
        "summary_dir": summary_dir,
        "plots_dir": plots_dir,
        "source_url": source_url,
        "target_url": target_url,
        "target_database_name": target_database_name,
    }


def run_benchmark(
    config_path: str | Path,
    source_engine: Engine | None = None,
    target_engine: Engine | None = None,
    target_database_name: str | None = None,
) -> BenchmarkResults:
    """
    Execute the benchmark matrix defined in a YAML configuration file.

    For each combination of (method, budget, seed):
    1. Run selection.
    2. Materialize the subset.
    3. Validate integrity and budget compliance.
    4. Evaluate the workload.
    5. Record timing, row counts, and per-query metrics.

    Saves:
    - Per-run results to `results/raw/`
    - Aggregated raw runs to `results/raw/all_benchmark_runs.csv`
    - Summary tables to `results/summaries/`
    - High-resolution evaluation plots to `results/plots/`
    """
    cfg = load_benchmark_config(config_path)
    methods = cfg["methods"]
    budget_percents = cfg["budget_percents"]
    seeds = cfg["seeds"]
    workload_path = Path(cfg["workload_path"])
    raw_dir = Path(cfg["raw_dir"])
    summary_dir = Path(cfg["summary_dir"])
    plots_dir = Path(cfg["plots_dir"])
    source_url = cfg.get("source_url")
    target_url = cfg.get("target_url")
    if target_database_name is None:
        target_database_name = cfg.get("target_database_name")

    raw_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    # Establish database connections if not provided
    created_engines = False
    if source_engine is None or target_engine is None:
        created_engines = True
        if source_url and target_url:
            if source_engine is None:
                source_engine = sa.create_engine(source_url)
            if target_engine is None:
                target_engine = sa.create_engine(target_url)
            if target_database_name is None:
                target_database_name = target_engine.url.database or "employees_target"
        else:
            app_cfg = load_config()
            if source_engine is None:
                source_engine = get_source_engine(app_cfg)
            if target_engine is None:
                target_engine = get_target_engine(app_cfg)
            if target_database_name is None:
                target_database_name = app_cfg.target_db.database

    if target_database_name is None:
        target_database_name = target_engine.url.database or "safeslicesql_target"

    try:
        if not test_connection(source_engine):
            raise ConnectionError(f"Cannot connect to source database engine: {source_engine.url}")
        if not test_connection(target_engine):
            raise ConnectionError(f"Cannot connect to target database engine: {target_engine.url}")

        console.print("[bold cyan]SafeSliceSQL Benchmark Suite Initializing...[/bold cyan]")
        console.print(f"Methods: [bold]{', '.join(methods)}[/bold]")
        console.print(f"Budgets: [bold]{', '.join(f'{b}%' for b in budget_percents)}[/bold]")
        console.print(f"Seeds: [bold]{', '.join(str(s) for s in seeds)}[/bold]")
        console.print(f"Workload: [bold]{workload_path}[/bold]")

        # Introspect schema and build dependency graph once
        schema = inspect_schema(source_engine)
        graph = build_dependency_graph(schema)
        total_source_rows = schema.total_row_count
        console.print(
            f"Source Database: [bold]{total_source_rows:,}[/bold] total rows across [bold]{len(schema.table_names)}[/bold] tables."
        )

        initial_source_snapshot = capture_source_snapshot(source_engine)
        if str(workload_path) == "auto":
            from safeslicesql.automatic_workload import save_automatic_workload

            workload_path = save_automatic_workload(schema, raw_dir / "workloads")
        workload_analysis = analyze_workload(workload_path, engine=source_engine)
        require_usable_workload(workload_analysis, for_selection="workload-aware" in methods)
        console.print(
            f"Parsed workload with [bold]{len(workload_analysis.queries)}[/bold] queries "
            f"([bold]{len(workload_analysis.supported_queries)}[/bold] supported)."
        )

        total_experiments = len(methods) * len(budget_percents) * len(seeds)
        console.print(
            f"\n[bold green]Running {total_experiments} total experiments...[/bold green]\n"
        )

        all_runs: list[BenchmarkRunResult] = []
        exp_index = 0

        for b_pct in budget_percents:
            budget_rows = max(1, int(total_source_rows * (b_pct / 100.0)))
            for method in methods:
                for seed in seeds:
                    exp_index += 1
                    run_id = f"{method}_b{b_pct}_s{seed}_{int(time.time() * 1000) % 1000000}"
                    console.print(
                        f"[{exp_index}/{total_experiments}] "
                        f"Method=[bold cyan]{method}[/bold cyan] | "
                        f"Budget=[bold]{b_pct}%[/bold] ({budget_rows:,} rows) | "
                        f"Seed=[bold]{seed}[/bold]... ",
                        end="",
                    )

                    run_err = ""
                    selection: SelectionResult | None = None
                    generation: GenerationResult | None = None
                    validation: ValidationReport | None = None
                    wl_report: WorkloadReport | None = None

                    t_sel = 0.0
                    t_gen = 0.0
                    t_val = 0.0
                    t_wl = 0.0

                    try:
                        # 1. Selection
                        t0 = time.perf_counter()
                        selection = select_rows(
                            schema=schema,
                            workload=workload_analysis,
                            graph=graph,
                            engine=source_engine,
                            budget=budget_rows,
                            method=method,
                            seed=seed,
                        )
                        t_sel = time.perf_counter() - t0

                        # 2. Materialization
                        t0 = time.perf_counter()
                        generation = materialize_subset(
                            source_engine=source_engine,
                            target_engine=target_engine,
                            schema=schema,
                            selection=selection,
                            graph=graph,
                            target_database=target_database_name,
                        )
                        t_gen = time.perf_counter() - t0

                        # 4. Workload Evaluation
                        if workload_analysis and len(workload_analysis.supported_queries) > 0:
                            t0 = time.perf_counter()
                            wl_report = evaluate_workload(
                                source_engine=source_engine,
                                target_engine=target_engine,
                                workload=workload_analysis,
                            )
                            t_wl = time.perf_counter() - t0

                        # 3. Validation
                        t0 = time.perf_counter()
                        validation = validate_subset(
                            source_engine=source_engine,
                            target_engine=target_engine,
                            budget_limit=budget_rows,
                            initial_source_row_count=total_source_rows,
                            initial_source_snapshot=initial_source_snapshot,
                        )
                        t_val = time.perf_counter() - t0

                        workload_failed = bool(wl_report and wl_report.failed_queries)
                        is_success = bool(
                            generation.success and validation.is_valid and not workload_failed
                        )
                        if is_success:
                            surv_pct = (
                                f"{wl_report.query_survival_rate * 100:.1f}%"
                                if wl_report
                                else "N/A"
                            )
                            console.print(
                                f"[green]SUCCESS[/green] (surv={surv_pct}, time={t_sel + t_gen + t_val + t_wl:.2f}s)"
                            )
                        else:
                            console.print("[yellow]FAILED VALIDATION[/yellow]")
                            run_err = "; ".join(
                                [
                                    generation.error_message,
                                    *validation.errors,
                                    *(
                                        [q.error for q in wl_report.per_query_results if q.error]
                                        if wl_report
                                        else []
                                    ),
                                ]
                            ).strip("; ")

                    except Exception as exc:
                        console.print(f"[red]CRASHED[/red] ({exc})")
                        run_err = str(exc)

                    run_res = BenchmarkRunResult(
                        run_id=run_id,
                        method=method,
                        seed=seed,
                        budget_percent=float(b_pct),
                        dataset_size=total_source_rows,
                        selection=selection,
                        generation=generation,
                        validation=validation,
                        workload=wl_report,
                        runtime_selection=t_sel,
                        runtime_materialization=t_gen,
                        runtime_validation=t_val,
                        runtime_workload_eval=t_wl,
                        success=bool(
                            validation
                            and validation.is_valid
                            and generation
                            and generation.success
                            and not run_err
                        ),
                        error_message=run_err,
                    )
                    all_runs.append(run_res)

                    # Export individual run CSV
                    save_run_results_csv(run_res, output_dir=raw_dir)

        # Aggregate all runs into all_benchmark_runs.csv and generate summaries
        benchmark_results = _compile_and_save_summaries(
            all_runs=all_runs,
            raw_dir=raw_dir,
            summary_dir=summary_dir,
            plots_dir=plots_dir,
            config_path=str(config_path),
        )

        return benchmark_results

    finally:
        if created_engines:
            source_engine.dispose()
            target_engine.dispose()


def _compile_and_save_summaries(
    all_runs: list[BenchmarkRunResult],
    raw_dir: Path,
    summary_dir: Path,
    plots_dir: Path,
    config_path: str,
) -> BenchmarkResults:
    """Aggregate individual runs, generate summary CSV and markdown, and produce plots."""
    rows_data: list[dict[str, Any]] = []
    for r in all_runs:
        sel = r.selection
        gen = r.generation
        val = r.validation
        wl = r.workload
        total_runtime = (
            r.runtime_selection
            + r.runtime_materialization
            + r.runtime_validation
            + r.runtime_workload_eval
        )

        rows_data.append(
            {
                "run_id": r.run_id,
                "method": r.method,
                "seed": r.seed,
                "budget_percent": r.budget_percent,
                "dataset_size": r.dataset_size,
                "success": r.success,
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
                "failed_queries": wl.failed_queries if wl else 0,
                "survival_denominator": wl.survival_denominator if wl else 0,
                "query_survival_rate": wl.query_survival_rate
                if wl and wl.supported_queries
                else None,
                "overall_key_retention": wl.overall_key_retention
                if wl and wl.supported_queries
                else None,
                "overall_result_retention": wl.overall_result_retention
                if wl and wl.supported_queries
                else None,
                "overall_aggregate_retention": wl.overall_aggregate_retention
                if wl and wl.supported_queries
                else None,
                "runtime_selection": r.runtime_selection,
                "runtime_materialization": r.runtime_materialization,
                "runtime_validation": r.runtime_validation,
                "runtime_workload_eval": r.runtime_workload_eval,
                "total_runtime": total_runtime,
                "error_message": r.error_message,
            }
        )

    # Save combined raw runs
    combined_raw_path = raw_dir / "all_benchmark_runs.csv"
    if rows_data:
        fieldnames = list(rows_data[0].keys())
        with open(combined_raw_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows_data)

    # Compute summary aggregates grouped by (method, budget_percent)
    try:
        import pandas as pd

        df = pd.DataFrame(rows_data)

        metric_cols = [
            "query_survival_rate",
            "overall_result_retention",
            "overall_key_retention",
            "overall_aggregate_retention",
            "closure_additions",
            "total_selected",
            "runtime_selection",
            "total_runtime",
        ]

        group_cols = ["method", "budget_percent"]
        counts = (
            df.groupby(group_cols)["success"]
            .agg(total_runs="size", successful_runs="sum")
            .reset_index()
        )
        counts["failed_runs"] = counts["total_runs"] - counts["successful_runs"]
        successful = df.loc[df["success"].eq(True)]
        metrics = (
            successful.groupby(group_cols)[metric_cols].agg(["mean", "std", "count"]).reset_index()
        )
        metrics.columns = [f"{a}_{b}" if b else a for a, b in metrics.columns]
        summary_df = counts.merge(metrics, on=group_cols, how="left")
        for metric in metric_cols:
            column = f"{metric}_count"
            summary_df[column] = summary_df[column].fillna(0).astype(int)

        summary_csv_path = summary_dir / "benchmark_summary.csv"
        summary_df.to_csv(summary_csv_path, index=False)

        # Generate markdown summary table
        md_path = summary_dir / "benchmark_summary.md"
        _write_markdown_summary(summary_df, md_path)

        # Generate evaluation plots
        try:
            from safeslicesql.plots import generate_all_plots

            generate_all_plots(summary_csv_path, output_dir=plots_dir)
        except Exception as exc:
            logger.warning("Could not generate plots: %s", exc)

        # Print console comparison table
        _print_console_summary_table(summary_df)

    except Exception as exc:
        logger.warning("Failed to compute Pandas summary table: %s", exc)

    successful_runs = sum(1 for r in all_runs if r.success)
    return BenchmarkResults(
        runs=all_runs,
        total_runs=len(all_runs),
        successful_runs=successful_runs,
        failed_runs=len(all_runs) - successful_runs,
        config_path=config_path,
    )


def _print_console_summary_table(summary_df: Any) -> None:
    """Display rich summary comparison table in console."""
    table = Table(
        title="SafeSliceSQL Benchmark Summary (Averaged Over Seeds)",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Method", style="cyan", justify="left")
    table.add_column("Budget", justify="center")
    table.add_column("Runs (OK / failed)", justify="center")
    table.add_column("Query Survival", justify="right", style="green")
    table.add_column("Result Retention", justify="right", style="green")
    table.add_column("Key Retention", justify="right", style="blue")
    table.add_column("Aggregate Ret.", justify="right", style="blue")
    table.add_column("Closure Add.", justify="right")
    table.add_column("Runtime (s)", justify="right")

    for _, row in summary_df.iterrows():
        method = str(row["method"])
        budget = f"{int(row['budget_percent'])}%"
        surv = _format_metric(row["query_survival_rate_mean"], 100, "%", 1)
        res_ret = _format_metric(row["overall_result_retention_mean"], 100, "%", 1)
        key_ret = _format_metric(row["overall_key_retention_mean"], 100, "%", 1)
        agg_ret = _format_metric(row["overall_aggregate_retention_mean"], 100, "%", 1)
        closure = _format_metric(row["closure_additions_mean"], 1, "", 1)
        runtime = _format_metric(row["total_runtime_mean"], 1, "s", 2)

        runs = f"{int(row['successful_runs'])} / {int(row['failed_runs'])}"
        table.add_row(method, budget, runs, surv, res_ret, key_ret, agg_ret, closure, runtime)

    console.print("\n")
    console.print(table)
    console.print("\n")


def _write_markdown_summary(summary_df: Any, md_path: Path) -> None:
    """Write publication-ready Markdown table summarizing experimental results."""
    lines = [
        "# SafeSliceSQL Experimental Evaluation Summary",
        "",
        "> Automated multi-seed benchmark matrix evaluating workload retention and overhead.",
        "",
        "| Method | Budget | Query Survival Rate | Result Retention | Key Retention | Aggregate Retention | Avg Closure Additions | Total Runtime |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for _, row in summary_df.iterrows():
        method = f"**{row['method']}**"
        budget = f"{int(row['budget_percent'])}%"
        surv = _format_metric(row["query_survival_rate_mean"], 100, "%", 1)
        res_ret = _format_metric(row["overall_result_retention_mean"], 100, "%", 1)
        key_ret = _format_metric(row["overall_key_retention_mean"], 100, "%", 1)
        agg_ret = _format_metric(row["overall_aggregate_retention_mean"], 100, "%", 1)
        closure = _format_metric(row["closure_additions_mean"], 1, "", 1)
        runtime = _format_metric(row["total_runtime_mean"], 1, "s", 2)

        lines.append(
            f"| {method} | {budget} | {surv} | {res_ret} | {key_ret} | {agg_ret} | {closure} | {runtime} |"
        )

    lines.append("")
    lines.append("## Observed Results")
    lines.append(
        "Means include successful runs only; missing measurements are N/A. Failed runs are reported separately. Single-run standard deviations are N/A."
    )
    for _, row in summary_df.iterrows():
        lines.append(
            f"- {row['method']}, {row['budget_percent']:g}%: {int(row['successful_runs'])}/{int(row['total_runs'])} successful runs; {int(row['failed_runs'])} failed."
        )
    for budget, group in summary_df.groupby("budget_percent"):
        measured = group.dropna(subset=["query_survival_rate_mean"])
        if measured.empty:
            lines.append(f"- Budget {budget:g}%: no successful query-survival measurements.")
            continue
        best = measured["query_survival_rate_mean"].max()
        winners = measured.loc[
            measured["query_survival_rate_mean"].map(lambda value: math.isclose(value, best)),
            "method",
        ].tolist()
        lines.append(
            f"- Budget {budget:g}%: highest observed mean query survival was {best * 100:.1f}% ({', '.join(winners)})."
        )
    lines.append(
        "These descriptive results do not establish statistical superiority or generalize beyond the measured runs."
    )
    lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _format_metric(value, scale=1, suffix="", precision=1):
    if value is None or not math.isfinite(float(value)):
        return "N/A"
    return f"{value * scale:.{precision}f}{suffix}"
