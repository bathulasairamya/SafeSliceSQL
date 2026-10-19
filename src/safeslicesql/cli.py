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
import sys
from typing import Optional

import typer

from safeslicesql.config import load_config
from safeslicesql.logging_config import setup_logging

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
        "--database", "-d",
        help="Source database name to inspect.",
    ),
    log_level: str = typer.Option(
        "INFO", "--log-level", "-l",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    ),
) -> None:
    """Inspect source database schema and foreign-key dependencies."""
    _init(log_level)
    logger.info("Inspecting database: %s", database)
    typer.echo(f"[inspect] Database: {database}")
    typer.echo("[inspect] Not yet implemented — Phase 2")
    raise typer.Exit(code=1)


@app.command()
def run(
    method: str = typer.Option(
        ...,
        "--method", "-m",
        help="Selection method: random-repair, referential, or workload-aware.",
    ),
    budget_percent: int = typer.Option(
        ...,
        "--budget-percent", "-b",
        help="Row budget as a percentage of total source rows (e.g., 20).",
    ),
    seed: int = typer.Option(
        42,
        "--seed", "-s",
        help="Random seed for reproducibility.",
    ),
    workload: str = typer.Option(
        "workloads/workload.sql",
        "--workload", "-w",
        help="Path to the SQL workload file.",
    ),
    log_level: str = typer.Option(
        "INFO", "--log-level", "-l",
        help="Logging level.",
    ),
) -> None:
    """Run subset selection and materialization with the specified method."""
    _init(log_level)

    valid_methods = ("random-repair", "referential", "workload-aware")
    if method not in valid_methods:
        typer.echo(
            f"[error] Unknown method '{method}'. "
            f"Valid methods: {', '.join(valid_methods)}",
            err=True,
        )
        raise typer.Exit(code=2)

    if not (1 <= budget_percent <= 100):
        typer.echo(
            f"[error] budget-percent must be between 1 and 100, "
            f"got {budget_percent}.",
            err=True,
        )
        raise typer.Exit(code=2)

    logger.info(
        "Run: method=%s, budget=%d%%, seed=%d, workload=%s",
        method, budget_percent, seed, workload,
    )

    typer.echo(f"[run] Method: {method}")
    typer.echo(f"[run] Budget: {budget_percent}%")
    typer.echo(f"[run] Seed: {seed}")
    typer.echo(f"[run] Workload: {workload}")
    typer.echo("[run] Not yet implemented — Phases 3-5")
    raise typer.Exit(code=1)


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
    log_level: str = typer.Option(
        "INFO", "--log-level", "-l",
        help="Logging level.",
    ),
) -> None:
    """Validate a generated subset for referential integrity and budget compliance."""
    _init(log_level)
    logger.info("Validating: source=%s, target=%s", source, target)
    typer.echo(f"[validate] Source: {source}")
    typer.echo(f"[validate] Target: {target}")
    typer.echo("[validate] Not yet implemented — Phase 6")
    raise typer.Exit(code=1)


@app.command()
def benchmark(
    config: str = typer.Option(
        "benchmarks/benchmarks.yaml",
        "--config", "-c",
        help="Path to the benchmark YAML configuration file.",
    ),
    log_level: str = typer.Option(
        "INFO", "--log-level", "-l",
        help="Logging level.",
    ),
) -> None:
    """Run the full benchmark matrix from a YAML configuration."""
    _init(log_level)
    logger.info("Running benchmark with config: %s", config)
    typer.echo(f"[benchmark] Config: {config}")
    typer.echo("[benchmark] Not yet implemented — Phase 8")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
