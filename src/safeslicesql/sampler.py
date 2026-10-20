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
    is_composite = len(pk_cols) > 1

    if isinstance(engine, dict):
        table_rows = engine.get(table_name, {})
        if isinstance(table_rows, dict):
            return _stable_pk_values(list(table_rows.keys()))
        if isinstance(table_rows, list):
            keys: list[Any] = []
            for row in table_rows:
                if isinstance(row, dict):
                    if is_composite:
                        val = tuple(row.get(c) for c in pk_cols)
                        if all(v is not None for v in val):
                            keys.append(val)
                    else:
                        val = row.get(pk_cols[0])
                        if val is not None:
                            keys.append(val)
            return _stable_pk_values(keys)
        return []

    if hasattr(engine, "connect"):
        try:
            with engine.connect() as conn:
                quoted_cols = ", ".join(f"`{c.replace('`', '``')}`" for c in pk_cols)
                quoted_table = f"`{table_name.replace('`', '``')}`"
                stmt = sa.text(f"SELECT {quoted_cols} FROM {quoted_table} ORDER BY {quoted_cols}")
                rows = conn.execute(stmt).mappings().all()
                if is_composite:
                    raw_keys = [tuple(row[c] for c in pk_cols) for row in rows]
                else:
                    raw_keys = [row[pk_cols[0]] for row in rows]
                return _stable_pk_values(raw_keys)
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
    closure_additions: int = 0,
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
        closure_additions=closure_additions,
        rejected_candidates=rejected_candidates,
        selection_log=selection_log,
    )


def select_rows(
    schema: SchemaMetadata,
    workload: WorkloadAnalysis | None = None,
    graph: DependencyGraph | None = None,
    engine: Engine | dict[str, Any] | None = None,
    budget: int = 0,
    method: str = "random-repair",
    seed: int = 42,
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
    workload : WorkloadAnalysis or None
        Parsed workload analysis (used by workload-aware method).
    graph : DependencyGraph or None
        Foreign-key dependency graph.
    engine : sqlalchemy.Engine or None
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

    graph = _ensure_graph(schema, graph)

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
    total_closure_additions = 0

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
        prev_total = _selection_total(selected)
        trial = {table: set(values) for table, values in selected.items()}
        trial.setdefault(table_name, set()).add(pk_value)
        closure = compute_closure(graph, trial, engine=engine, budget=budget)

        if closure.is_feasible:
            selected = {table: set(values) for table, values in closure.selected_rows.items()}
            new_total = _selection_total(selected)
            total_closure_additions += max(0, new_total - prev_total - 1)
            selection_log.append(
                f"Accepted {table_name}.{pk_value}: "
                f"closure={new_total} rows (+{new_total - prev_total})"
            )
            if new_total >= budget:
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
        closure_additions=total_closure_additions,
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
    total_closure_additions = 0
    rng = random.Random(seed)

    for table_name in table_order:
        pk_values = _all_primary_keys_for_table(schema, table_name, engine)
        rng.shuffle(pk_values)
        for pk_value in pk_values:
            prev_total = _selection_total(selected)
            trial = {table: set(values) for table, values in selected.items()}
            trial.setdefault(table_name, set()).add(pk_value)
            closure = compute_closure(graph, trial, engine=engine, budget=budget)

            if closure.is_feasible:
                selected = {table: set(values) for table, values in closure.selected_rows.items()}
                new_total = _selection_total(selected)
                total_closure_additions += max(0, new_total - prev_total - 1)
                selection_log.append(
                    f"Added {table_name}.{pk_value}: "
                    f"closure={new_total} rows (+{new_total - prev_total})"
                )
                if new_total >= budget:
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
        closure_additions=total_closure_additions,
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

    # Pre-fetch candidate primary keys once to avoid repeated DB queries in loop
    cached_pks: dict[str, list[Any]] = {}
    for table_name in schema.table_names:
        cached_pks[table_name] = _all_primary_keys_for_table(schema, table_name, engine)

    # Order tables by workload weight (highest weight first)
    sorted_tables = sorted(
        schema.table_names,
        key=lambda t: (-table_weights.get(t, 0.0), t),
    )

    # Build candidate pool ordered by table weight, shuffled within tables
    rng = random.Random(seed)
    remaining: list[tuple[str, Any]] = []
    for table_name in sorted_tables:
        pks = list(cached_pks.get(table_name, []))
        rng.shuffle(pks)
        for pk in pks:
            remaining.append((table_name, pk))

    # Windowed greedy selection: evaluate batches of candidates and pick
    # the highest-scoring feasible one each round. This faithfully implements
    # the greedy heuristic: score(c|S) = ΔV(c|S) / max(1, ΔD(c|S)).
    rejected_candidates = 0
    total_closure_additions = 0
    greedy_window = 50

    while remaining and _selection_total(selected) < budget:
        window = remaining[:greedy_window]
        if not window:
            break

        current_total = _selection_total(selected)
        best_score = -1.0
        best_entry: tuple | None = None
        indices_to_remove: set[int] = set()

        for i, (table_name, pk_value) in enumerate(window):
            if pk_value in selected.get(table_name, set()):
                indices_to_remove.add(i)
                continue

            trial = {tbl: set(values) for tbl, values in selected.items()}
            trial.setdefault(table_name, set()).add(pk_value)
            closure = compute_closure(graph, trial, engine=engine, budget=budget)

            if not closure.is_feasible:
                rejected_candidates += 1
                indices_to_remove.add(i)
                selection_log.append(
                    f"Skipped {table_name}.{pk_value}: "
                    f"{closure.infeasibility_reason or 'budget exceeded'}"
                )
                continue

            additional_rows = max(1, closure.total_rows - current_total)
            score = table_weights.get(table_name, 1.0) / additional_rows

            if score > best_score:
                best_score = score
                best_entry = (i, table_name, pk_value, closure.selected_rows)

        if best_entry is not None:
            idx, table_name, pk_value, closure_rows = best_entry
            selected = {tbl: set(values) for tbl, values in closure_rows.items()}
            new_total = _selection_total(selected)
            additions = max(0, new_total - current_total - 1)
            total_closure_additions += additions
            selection_log.append(
                f"Selected {table_name}.{pk_value}: score={best_score:.3f}, "
                f"deps=+{additions}, total_rows={new_total}"
            )
            indices_to_remove.add(idx)

        # Remove processed candidates from remaining
        remaining = [
            item for j, item in enumerate(remaining)
            if j >= len(window) or j not in indices_to_remove
        ]

        if best_entry is None and not indices_to_remove:
            break  # No progress possible

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
        closure_additions=total_closure_additions,
        rejected_candidates=rejected_candidates,
        selection_log=selection_log,
    )
