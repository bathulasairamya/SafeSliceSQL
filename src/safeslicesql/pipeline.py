"""Shared selection, materialization, evaluation, and validation orchestration."""

from __future__ import annotations

import time
from typing import Callable

from sqlalchemy import Engine

from safeslicesql.acceptance import AcceptancePolicy
from safeslicesql.connection_scope import ConnectionScope, source_transaction
from safeslicesql.dependency_graph import DependencyGraph
from safeslicesql.runtime import checkpoint, current
from safeslicesql.sampler import select_rows
from safeslicesql.security import safe_error
from safeslicesql.source_snapshot import SourceSnapshot, capture_source_snapshot
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.target_lock import serialized_target
from safeslicesql.types import BenchmarkRunResult, GenerationResult, SchemaMetadata
from safeslicesql.validator import evaluate_workload, validate_subset
from safeslicesql.workload_analyzer import WorkloadAnalysis


@serialized_target
def run_pipeline(
    *,
    source_engine: Engine,
    target_engine: Engine,
    schema: SchemaMetadata,
    graph: DependencyGraph,
    workload: WorkloadAnalysis,
    budget: int,
    method: str,
    seed: int,
    run_id: str,
    budget_percent: float,
    target_database: str,
    initial_source_snapshot: SourceSnapshot,
    acceptance: AcceptancePolicy | None = None,
    on_stage: Callable[[str, object], None] | None = None,
) -> BenchmarkRunResult:
    """Return complete failure evidence; never evaluate a failed materialization."""
    result = BenchmarkRunResult(run_id, method, seed, budget_percent, schema.total_row_count)
    errors = []

    def stage(name, action):
        checkpoint()
        if (guard := current()) and guard.job:
            guard.job.phase(name)
        started = time.perf_counter()
        try:
            value = action()
            setattr(result, name, value)
        finally:
            timing = {
                "selection": "runtime_selection",
                "generation": "runtime_materialization",
                "workload": "runtime_workload_eval",
                "validation": "runtime_validation",
            }[name]
            setattr(result, timing, time.perf_counter() - started)
        checkpoint()
        if on_stage:
            on_stage(name, value)
        return value

    def verify(source_connection, target_connection):
        # These borrowed views see uncommitted rows without closing either transaction.
        source_view = ConnectionScope(source_connection)
        source_view.verified_snapshot = initial_source_snapshot
        target_view = ConnectionScope(target_connection)
        if on_stage:
            on_stage(
                "generation",
                GenerationResult(
                    target_database,
                    rows_inserted={
                        name: len(keys) for name, keys in selection.selected_rows.items()
                    },
                    total_rows_inserted=selection.total_selected,
                    insertion_order=graph.topological_order(),
                    pending_validation=True,
                ),
            )
        try:
            evaluated = stage(
                "workload", lambda: evaluate_workload(source_view, target_view, workload)
            )
            errors.extend(query.error for query in evaluated.per_query_results if query.error)
            if evaluated.failed_queries and not errors:
                errors.append("Workload execution failed.")
        except Exception as exc:
            errors.append(f"Workload evaluation failed: {safe_error(exc)}")
        validation = stage(
            "validation",
            lambda: validate_subset(
                source_engine=source_view,
                target_engine=target_view,
                budget_limit=budget,
                initial_source_row_count=schema.total_row_count,
                initial_source_snapshot=initial_source_snapshot,
            ),
        )
        errors.extend(validation.errors)
        if result.workload is not None:
            result.acceptance = (acceptance or AcceptancePolicy()).evaluate(result.workload)
            errors.extend(result.acceptance.errors)
        if not validation.is_valid and not validation.errors:
            errors.append("Validation checks failed.")
        if errors:
            raise ValueError("; ".join(errors))

    try:
        with source_transaction(source_engine) as source_view:
            # Establish the read view before any selection or target changes.
            if capture_source_snapshot(source_view) != initial_source_snapshot:
                raise ValueError(
                    "Source changed since the baseline; restart with a fresh baseline."
                )
            if initial_source_snapshot.row_count != schema.total_row_count:
                raise ValueError(
                    "Source row counts changed during setup; restart with a fresh baseline."
                )
            source_view.verified_row_counts = dict(initial_source_snapshot.table_counts) or {
                name: table.row_count for name, table in schema.tables.items()
            }
            selection = stage(
                "selection",
                lambda: select_rows(
                    schema=schema,
                    workload=workload,
                    graph=graph,
                    engine=source_view,
                    budget=budget,
                    method=method,
                    seed=seed,
                ),
            )
            started = time.perf_counter()
            if (guard := current()) and guard.job:
                guard.job.phase("generation")
            try:
                generation = materialize_subset(
                    source_engine=source_view,
                    target_engine=target_engine,
                    schema=schema,
                    selection=selection,
                    graph=graph,
                    target_database=target_database,
                    before_commit=verify,
                )
                result.generation = generation
            finally:
                result.runtime_materialization = max(
                    0.0,
                    time.perf_counter()
                    - started
                    - result.runtime_workload_eval
                    - result.runtime_validation,
                )
        if not generation.success:
            result.error_message = generation.error_message or "Materialization failed."
            return result
        result.success = result.validation is not None and result.validation.is_valid and not errors
    except Exception as exc:
        errors.append(safe_error(exc))
    result.error_message = "; ".join(error for error in errors if error)
    return result
