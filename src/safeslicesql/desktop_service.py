"""Desktop actions reuse dataset preparation and the validated subset pipeline."""

from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from safeslicesql.acceptance import AcceptancePolicy
from safeslicesql.benchmark import run_benchmark
from safeslicesql.config import AppConfig
from safeslicesql.dataset_input import PreparedDataset, prepare_dataset
from safeslicesql.experiment_config import write_experiment_config
from safeslicesql.jobs import journal
from safeslicesql.runtime import Limits, bounded_engine, execution_context
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
    workload: str | None = None
    min_query_survival: float = 1.0
    min_result_retention: float = 0.0
    required_queries: tuple[str, ...] = ()

    def validate(self):
        AcceptancePolicy(self.min_query_survival, self.min_result_retention, self.required_queries)
        if self.workload is not None and not Path(self.workload).is_file():
            raise ValueError("Choose an existing application workload SQL file.")
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


def _create_copy(
    config: AppConfig,
    request: CopyRequest,
    output: Path,
    progress: Callable[[str], None] = lambda _: None,
) -> CopyOutcome:
    request.validate()
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
        workload=request.workload or str(prepared.workload),
        acceptance=asdict(
            AcceptancePolicy(
                request.min_query_survival, request.min_result_retention, request.required_queries
            )
        ),
        output=output,
    )
    engines = []
    try:
        engines.append(bounded_engine(prepared.source.url, read_only=True))
        engines.append(bounded_engine(prepared.target.url))
        progress("Selecting records, creating the copy, and checking the results…")
        results = run_benchmark(path, *engines, target_database_name=prepared.target.database)
        return CopyOutcome(request, prepared, results, output)
    finally:
        for engine in engines:
            engine.dispose()


def create_copy(
    config: AppConfig,
    request: CopyRequest,
    output: Path,
    progress: Callable[[str], None] = lambda _: None,
    *,
    limits: Limits | None = None,
    cancellation: Event | None = None,
) -> CopyOutcome:
    request.validate()
    output.mkdir(parents=True, exist_ok=False)
    limits = limits or Limits.from_env()
    with journal(output, asdict(request)) as job:
        job.record["limits"] = asdict(limits)
        job.save()
        with execution_context(limits, cancellation, job):
            outcome = _create_copy(config, request, output, progress)
            if outcome.results.failed_runs:
                job.finish(
                    "rejected"
                    if any(
                        run.acceptance and not run.acceptance.passed for run in outcome.results.runs
                    )
                    else "failed",
                    "; ".join(
                        run.error_message for run in outcome.results.runs if run.error_message
                    ),
                )
            return outcome
