"""
safeslicesql.benchmark — Benchmark runner and experiment management.

Executes the benchmark matrix across methods, dataset sizes, budgets,
and seeds. Records all results to structured CSV files for reproducible
analysis and plotting.

Owner: M3
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("safeslicesql.benchmark")


def run_benchmark(config_path: str | Path) -> None:
    """
    Execute the benchmark matrix defined in a YAML configuration file.

    For each combination of (method, dataset_size, budget, seed):
    1. Run selection.
    2. Materialize the subset.
    3. Validate integrity.
    4. Evaluate the workload.
    5. Record timing, row counts, and per-query metrics.

    Saves:
    - Per-run results to `results/raw/`
    - Summary tables to `results/summaries/`

    Parameters
    ----------
    config_path : str or Path
        Path to the benchmark YAML configuration file.
    """
    raise NotImplementedError("Phase 8: implement benchmark runner")
