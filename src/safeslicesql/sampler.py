"""
safeslicesql.sampler — Row selection methods.

Implements the three selection strategies:
1. Random sampling with dependency repair (Baseline A)
2. Referential/table-aware sampling (Baseline B)
3. SafeSliceSQL workload-aware greedy selection (Proposed)

All methods share the same interface, budget enforcement, and
dependency-closure pipeline for fair comparison.

Owner: M2
"""

from __future__ import annotations

import logging
import random
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine

from safeslicesql.dependency_graph import (
    DependencyGraph,
    build_dependency_graph,
    compute_closure,
)
from safeslicesql.types import SchemaMetadata, SelectionResult
from safeslicesql.workload_analyzer import WorkloadAnalysis

logger = logging.getLogger("safeslicesql.sampler")

# Valid method identifiers
METHODS = ("random-repair", "referential", "workload-aware")


def _ensure_graph(
    schema: SchemaMetadata,
    graph: DependencyGraph | None,
) -> DependencyGraph:
    """Return the schema graph, constructing one when needed."""
    if graph is not None:
        return graph
    return build_dependency_graph(schema)


def _stable_pk_values(values: list[Any]) -> list[Any]:
    """Sort primary keys deterministically across mixed types."""
    return sorted(values, key=lambda value: (type(value).__name__, str(value)))


def _all_primary_keys_for_table(
    schema: SchemaMetadata,
    table_name: str,
    engine: Engine | dict[str, Any] | None,
) -> list[Any]:
    """Collect all row primary keys for a table from engine metadata or a mock dictionary."""
    table_meta = schema.tables.get(table_name)
    if table_meta is None:
        return []

    pk_cols = table_meta.effective_primary_keys or ["id"]
    pk_col = pk_cols[0]

    if isinstance(engine, dict):
        table_rows = engine.get(table_name, {})
        if isinstance(table_rows, dict):
            return _stable_pk_values(list(table_rows.keys()))
        if isinstance(table_rows, list):
            keys: list[Any] = []
            for row in table_rows:
                if isinstance(row, dict):
                    value = row.get(pk_col)
                    if value is not None:
                        keys.append(value)
            return _stable_pk_values(keys)
        return []

    if hasattr(engine, "connect"):
        try:
            with engine.connect() as conn:
                stmt = sa.text(f"SELECT `{pk_col}` FROM `{table_name}` ORDER BY `{pk_col}`")
                rows = conn.execute(stmt).mappings().all()
                return _stable_pk_values([row[pk_col] for row in rows])
        except Exception as exc:  # pragma: no cover - live DB fallback
            logger.warning("Unable to fetch PKs for table '%s': %s", table_name, exc)
            return []

    return []


def _selection_total(selected_rows: dict[str, set[Any] | list[Any]]) -> int:
    """Return the number of rows currently selected."""
    return sum(len(values) for values in selected_rows.values())


def _finalize_selection(
    *,
    method: str,
    seed: int,
    budget: int,
    selected: dict[str, set[Any]],
    rejected_candidates: int,
    selection_log: list[str],
) -> SelectionResult:
    """Turn a closure dictionary into a normalized SelectionResult."""
    normalized: dict[str, list[Any]] = {}
    for table_name in sorted(selected):
        normalized[table_name] = _stable_pk_values(list(selected[table_name]))
    total_selected = _selection_total(normalized)
    return SelectionResult(
        method=method,
        seed=seed,
        budget=budget,
        selected_rows=normalized,
        total_selected=total_selected,
        closure_additions=max(0, total_selected - sum(len(v) for v in selected.values() if False)),
        rejected_candidates=rejected_candidates,
        selection_log=selection_log,
    )


def select_rows(
    schema: SchemaMetadata,
    workload: WorkloadAnalysis,
    graph: DependencyGraph,
    engine: Engine,
    budget: int,
    method: str,
    seed: int,
) -> SelectionResult:
    """
    Select rows using the specified method.

    All methods:
    1. Propose candidate/seed rows.
    2. Compute dependency closure for each candidate.
    3. Enforce the hard row budget (reject infeasible candidates).
    4. Return the final selection with logging.

    Parameters
    ----------
    schema : SchemaMetadata
        Source database schema metadata.
    workload : WorkloadAnalysis
        Parsed workload analysis (used by workload-aware method).
    graph : DependencyGraph
        Foreign-key dependency graph.
    engine : sqlalchemy.Engine
        Source database engine.
    budget : int
        Maximum total rows allowed in the subset.
    method : str
        Selection method: 'random-repair', 'referential', or
        'workload-aware'.
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    SelectionResult
        Selected rows with metadata.

    Raises
    ------
    ValueError
        If *method* is not one of the supported methods.
    """
    if method not in METHODS:
        raise ValueError(
            f"Unknown method '{method}'. "
            f"Supported methods: {', '.join(METHODS)}"
        )

    if method == "random-repair":
        return _select_random_repair(schema, graph, engine, budget, seed)
    elif method == "referential":
        return _select_referential(schema, graph, engine, budget, seed)
    else:
        return _select_workload_aware(
            schema, workload, graph, engine, budget, seed,
        )


def _select_random_repair(
    schema: SchemaMetadata,
    graph: DependencyGraph | None,
    engine: Engine | dict[str, Any] | None,
    budget: int,
    seed: int,
) -> SelectionResult:
    """
    Baseline A: Random sampling with dependency repair.

    1. Select seed rows randomly using the recorded seed.
    2. Compute dependency closure.
    3. Reject infeasible candidates under the hard budget.
    """
    graph = _ensure_graph(schema, graph)
    selected: dict[str, set[Any]] = {table: set() for table in schema.table_names}
    selection_log: list[str] = [f"Random repair baseline with seed={seed}"]
    rejected_candidates = 0

    candidate_pairs: list[tuple[str, Any]] = []
    for table_name in schema.table_names:
        for pk_value in _all_primary_keys_for_table(schema, table_name, engine):
            candidate_pairs.append((table_name, pk_value))

    if not candidate_pairs:
        return SelectionResult(
            method="random-repair",
            seed=seed,
            budget=budget,
            selected_rows={},
            total_selected=0,
            closure_additions=0,
            rejected_candidates=0,
            selection_log=selection_log,
        )

    rng = random.Random(seed)
    rng.shuffle(candidate_pairs)

    for table_name, pk_value in candidate_pairs:
        trial = {table: set(values) for table, values in selected.items()}
        trial.setdefault(table_name, set()).add(pk_value)
        closure = compute_closure(graph, trial, engine=engine, budget=budget)

        if closure.is_feasible:
            selected = {table: set(values) for table, values in closure.selected_rows.items()}
            selection_log.append(
                f"Accepted {table_name}.{pk_value}: closure={sum(len(values) for values in selected.values())} rows"
            )
            if _selection_total(selected) >= budget:
                break
            continue

        rejected_candidates += 1
        selection_log.append(
            f"Rejected {table_name}.{pk_value}: {closure.infeasibility_reason or 'closure exceeds budget'}"
        )

    return SelectionResult(
        method="random-repair",
        seed=seed,
        budget=budget,
        selected_rows={
            table: _stable_pk_values(list(values))
            for table, values in selected.items()
            if values
        },
        total_selected=_selection_total(selected),
        closure_additions=0,
        rejected_candidates=rejected_candidates,
        selection_log=selection_log,
    )


def _select_referential(
    schema: SchemaMetadata,
    graph: DependencyGraph | None,
    engine: Engine | dict[str, Any] | None,
    budget: int,
    seed: int,
) -> SelectionResult:
    """
    Baseline B: Referential/table-aware sampling.

    1. Use a documented table-aware seed-selection policy.
    2. Compute dependency closure.
    3. Enforce the same hard budget.
    """
    graph = _ensure_graph(schema, graph)
    try:
        table_order = list(graph.topological_order())
    except ValueError:
        table_order = schema.table_names

    table_order = [table for table in table_order if table in schema.tables]
    table_order.extend(table for table in schema.table_names if table not in table_order)

    selected: dict[str, set[Any]] = {table: set() for table in schema.table_names}
    selection_log: list[str] = [f"Referential baseline with seed={seed}"]
    rejected_candidates = 0

    for table_name in table_order:
        pk_values = _all_primary_keys_for_table(schema, table_name, engine)
        for pk_value in pk_values:
            trial = {table: set(values) for table, values in selected.items()}
            trial.setdefault(table_name, set()).add(pk_value)
            closure = compute_closure(graph, trial, engine=engine, budget=budget)

            if closure.is_feasible:
                selected = {table: set(values) for table, values in closure.selected_rows.items()}
                selection_log.append(
                    f"Added {table_name}.{pk_value}: closure={sum(len(values) for values in selected.values())} rows"
                )
                if _selection_total(selected) >= budget:
                    break
                continue

            rejected_candidates += 1
            selection_log.append(
                f"Skipped {table_name}.{pk_value}: {closure.infeasibility_reason or 'closure exceeds budget'}"
            )

        if _selection_total(selected) >= budget:
            break

    return SelectionResult(
        method="referential",
        seed=seed,
        budget=budget,
        selected_rows={
            table: _stable_pk_values(list(values))
            for table, values in selected.items()
            if values
        },
        total_selected=_selection_total(selected),
        closure_additions=0,
        rejected_candidates=rejected_candidates,
        selection_log=selection_log,
    )


def _select_workload_aware(
    schema: SchemaMetadata,
    workload: WorkloadAnalysis | None,
    graph: DependencyGraph | None,
    engine: Engine | dict[str, Any] | None,
    budget: int,
    seed: int,
) -> SelectionResult:
    """
    Proposed method: SafeSliceSQL workload-aware greedy selection.

    score(c | S) = ΔV(c | S) / max(1, ΔD(c | S))

    Where:
    - ΔV = marginal workload-retention value
    - ΔD = additional rows required by dependency closure
    """
    graph = _ensure_graph(schema, graph)
    selected: dict[str, set[Any]] = {table: set() for table in schema.table_names}
    selection_log: list[str] = [f"Workload-aware selector with seed={seed}"]

    if workload is None:
        workload = WorkloadAnalysis()

    table_weights: dict[str, float] = {}
    for query in workload.queries:
        if not query.is_supported:
            continue
        for table_name in query.tables:
            table_weights[table_name] = table_weights.get(table_name, 0.0) + 2.0
        if query.join_tables:
            table_weights[query.tables[0]] = table_weights.get(query.tables[0], 0.0) + 0.5 * len(query.join_tables)
        if query.filter_columns:
            for table_name, _ in query.filter_columns:
                if table_name in schema.tables:
                    table_weights[table_name] = table_weights.get(table_name, 0.0) + 1.0
        if query.group_columns or query.aggregate_functions:
            for table_name, _ in query.group_columns:
                if table_name in schema.tables:
                    table_weights[table_name] = table_weights.get(table_name, 0.0) + 1.5
            table_weights[query.tables[0]] = table_weights.get(query.tables[0], 0.0) + 1.0

    if not table_weights:
        table_weights = {table_name: 1.0 for table_name in schema.table_names}

    rejected_candidates = 0
    while True:
        current_total = _selection_total(selected)
        if current_total >= budget:
            break

        best_choice: tuple[float, str, Any] | None = None
        for table_name in schema.table_names:
            for pk_value in _all_primary_keys_for_table(schema, table_name, engine):
                if pk_value in selected.get(table_name, set()):
                    continue

                trial = {tbl: set(values) for tbl, values in selected.items()}
                trial.setdefault(table_name, set()).add(pk_value)
                closure = compute_closure(graph, trial, engine=engine, budget=budget)
                if not closure.is_feasible:
                    rejected_candidates += 1
                    continue

                additional_rows = max(1, closure.total_rows - current_total)
                workload_value = table_weights.get(table_name, 1.0)
                score = workload_value / additional_rows

                tie_key = (table_name, str(pk_value))
                candidate = (score, tie_key[0], tie_key[1])
                if best_choice is None or candidate[0] > best_choice[0] or (
                    abs(candidate[0] - best_choice[0]) < 1e-12 and tie_key < (best_choice[1], str(best_choice[2]))
                ):
                    best_choice = (score, table_name, pk_value)

        if best_choice is None:
            break

        _, table_name, pk_value = best_choice
        selected.setdefault(table_name, set()).add(pk_value)
        closure = compute_closure(graph, selected, engine=engine, budget=budget)
        if not closure.is_feasible:
            selected[table_name].discard(pk_value)
            rejected_candidates += 1
            selection_log.append(f"Skipped {table_name}.{pk_value}: budget infeasible after closure")
            break

        selected = {tbl: set(values) for tbl, values in closure.selected_rows.items()}
        selection_log.append(
            f"Selected {table_name}.{pk_value}: score={best_choice[0]:.3f}, total_rows={_selection_total(selected)}"
        )

    return SelectionResult(
        method="workload-aware",
        seed=seed,
        budget=budget,
        selected_rows={
            table: _stable_pk_values(list(values))
            for table, values in selected.items()
            if values
        },
        total_selected=_selection_total(selected),
        closure_additions=0,
        rejected_candidates=rejected_candidates,
        selection_log=selection_log,
    )
