"""
safeslicesql.reporting — Result formatting, CSV export, and logging utilities.

Provides functions to format validation reports, workload reports, and
benchmark results into structured output (CSV, console tables, logs).

Owner: M3
"""

from __future__ import annotations

import logging
from pathlib import Path

from safeslicesql.types import (
    BenchmarkRunResult,
    GenerationResult,
    SelectionResult,
    ValidationReport,
    WorkloadReport,
)

logger = logging.getLogger("safeslicesql.reporting")


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
    raise NotImplementedError("Phase 7: implement CSV export")


def print_validation_summary(report: ValidationReport) -> None:
    """Print a human-readable validation summary to the console."""
    raise NotImplementedError("Phase 7: implement validation summary")


def print_workload_summary(report: WorkloadReport) -> None:
    """Print a human-readable workload evaluation summary."""
    raise NotImplementedError("Phase 7: implement workload summary")


def print_selection_summary(result: SelectionResult) -> None:
    """Print a human-readable selection summary."""
    raise NotImplementedError("Phase 7: implement selection summary")


def print_generation_summary(result: GenerationResult) -> None:
    """Print a human-readable materialization summary."""
    raise NotImplementedError("Phase 7: implement generation summary")
