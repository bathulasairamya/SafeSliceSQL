"""Desktop actions reuse dataset preparation and the validated subset pipeline."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import sqlalchemy as sa

from safeslicesql.benchmark import run_benchmark
from safeslicesql.config import AppConfig
from safeslicesql.dataset_input import PreparedDataset, prepare_dataset
from safeslicesql.experiment_config import write_experiment_config
from safeslicesql.sampler import METHODS
from safeslicesql.types import BenchmarkResults


@dataclass(frozen=True)
class CopyRequest:
    kind: str
    source: str
    budget: int = 10
    method: str = "workload-aware"
    seed: int = 42
    compare: bool = False

    def validate(self):
        if self.kind not in {"mysql", "csv", "folder", "xlsx"}:
            raise ValueError("Choose a supported data source.")
        if not self.source.strip():
            raise ValueError("Choose your database or file first.")
        if not 1 <= self.budget <= 100:
            raise ValueError("Choose a percentage between 1 and 100.")
        if self.method not in METHODS:
            raise ValueError("Choose a valid selection method.")


@dataclass
class CopyOutcome:
    request: CopyRequest
    prepared: PreparedDataset
    results: BenchmarkResults
    folder: Path


def create_copy(
    config: AppConfig,
    request: CopyRequest,
    output: Path,
    progress: Callable[[str], None] = lambda _: None,
) -> CopyOutcome:
    request.validate()
    output.mkdir(parents=True, exist_ok=False)
    progress("Loading your data and preparing a separate copy…")
    prepared = prepare_dataset(
        config,
        output / "dataset",
        database=request.source if request.kind == "mysql" else None,
        files=None if request.kind == "mysql" else Path(request.source),
    )
    path = write_experiment_config(
        output / "run.yaml",
        methods=list(METHODS) if request.compare else [request.method],
        budgets=[request.budget],
        seeds=[request.seed],
        workload=str(prepared.workload),
        output=output,
    )
    engines = []
    try:
        engines.append(sa.create_engine(prepared.source.url))
        engines.append(sa.create_engine(prepared.target.url))
        progress("Selecting records, creating the copy, and checking the results…")
        results = run_benchmark(path, *engines, target_database_name=prepared.target.database)
        return CopyOutcome(request, prepared, results, output)
    finally:
        for engine in engines:
            engine.dispose()
