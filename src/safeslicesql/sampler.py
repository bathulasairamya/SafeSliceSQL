"""
safeslicesql.sampler — Row selection methods.

Implements the three selection strategies:
1. Random sampling with dependency repair (Baseline A)
2. Referential/table-aware sampling (Baseline B)
3. SafeSliceSQL workload-aware greedy selection (Proposed)

All methods share the same interface, budget enforcement, and
dependency-closure pipeline for fair comparison.

"""

from __future__ import annotations

import logging
import math
import random
from itertools import islice
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine

from safeslicesql.dependency_graph import (
    DependencyGraph,
    build_dependency_graph,
    clear_fk_cache,
    compute_closure,
    prime_foreign_key_cache,
)
from safeslicesql.runtime import checkpoint
from safeslicesql.types import SchemaMetadata, SelectionResult
from safeslicesql.workload_analyzer import WorkloadAnalysis, require_usable_workload
from safeslicesql.workload_candidates import query_witnesses

logger = logging.getLogger("safeslicesql.sampler")

# Valid method identifiers
METHODS = ("random-repair", "referential", "workload-aware")
CANDIDATE_POOL_MULTIPLIER = 1.25
SELECTION_LOG_LIMIT = 1000
WITNESS_PREFETCH_SIZE = 64


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

    return []


def _selection_total(selected_rows: dict[str, set[Any] | list[Any]]) -> int:
    """Return the number of rows currently selected."""
    return sum(len(values) for values in selected_rows.values())


def _sample_primary_keys(
    schema: SchemaMetadata,
    table_name: str,
    engine: Engine | dict[str, Any] | None,
    sample_size: int,
    rng: random.Random,
) -> list[Any]:
    """Select a reproducible, bounded reservoir of primary keys from a table."""
    if sample_size <= 0:
        return []
    if isinstance(engine, dict) or engine is None or not hasattr(engine, "connect"):
        keys = _all_primary_keys_for_table(schema, table_name, engine)
        if len(keys) <= sample_size:
            return keys
        return rng.sample(keys, sample_size)

    table_meta = schema.tables[table_name]
    pk_columns = table_meta.effective_primary_keys or ["id"]
    quoted_table = f"`{table_name.replace('`', '``')}`"
    quoted_columns = ", ".join(f"`{column.replace('`', '``')}`" for column in pk_columns)
    statement = sa.text(f"SELECT {quoted_columns} FROM {quoted_table} ORDER BY {quoted_columns}")
    reservoir: list[Any] = []

    with engine.connect() as conn:
        # Rejection sampling over a dense integer key space is uniform without
        # scanning the whole index. Sparse/noninteger/composite keys fall back
        # to the exact streaming reservoir below.
        column = table_meta.get_column(pk_columns[0]) if len(pk_columns) == 1 else None
        if column and "INT" in column.data_type.upper() and sample_size * 4 < table_meta.row_count:
            low, high = conn.execute(
                sa.text(f"SELECT MIN({quoted_columns}), MAX({quoted_columns}) FROM {quoted_table}")
            ).one()
            if (
                isinstance(low, int)
                and isinstance(high, int)
                and high - low + 1 <= 4 * table_meta.row_count
            ):
                found, attempted = [], set()
                for _ in range(math.ceil(sample_size * 8 / 500)):
                    checkpoint()
                    guesses = []
                    for _ in range(min(500, sample_size * 8 - len(attempted))):
                        key = rng.randint(low, high)
                        if key not in attempted:
                            attempted.add(key)
                            guesses.append(key)
                    if not guesses:
                        break
                    statement_keys = sa.text(
                        f"SELECT {quoted_columns} FROM {quoted_table} WHERE {quoted_columns} IN :keys"
                    ).bindparams(sa.bindparam("keys", expanding=True))
                    matches = set(conn.execute(statement_keys, {"keys": guesses}).scalars())
                    found.extend(key for key in guesses if key in matches)
                    if len(found) >= sample_size:
                        return found[:sample_size]
        result = conn.execution_options(stream_results=True).execute(statement).mappings()
        for row_index, row in enumerate(result):
            checkpoint()
            key = (
                tuple(row[column] for column in pk_columns)
                if len(pk_columns) > 1
                else row[pk_columns[0]]
            )
            if row_index < sample_size:
                reservoir.append(key)
            else:
                replacement = rng.randrange(row_index + 1)
                if replacement < sample_size:
                    reservoir[replacement] = key
            if row_index > 0 and row_index % 500_000 == 0:
                logger.info(
                    "Scanned %d rows from '%s' while sampling primary keys",
                    row_index,
                    table_name,
                )
        result.close()

    logger.info(
        "Sampled %d candidate keys from '%s'",
        len(reservoir),
        table_name,
    )
    return reservoir


def _candidate_rows_by_table(
    schema: SchemaMetadata,
    engine: Engine | dict[str, Any] | None,
    budget: int,
    seed: int,
) -> dict[str, list[Any]]:
    """Build a seeded candidate pool capped near the requested row budget."""
    if budget <= 0:
        return {name: [] for name in schema.table_names}
    rng = random.Random(seed)
    row_counts: dict[str, int] = {}

    for table_name in schema.table_names:
        if isinstance(engine, dict):
            table_data = engine.get(table_name, {})
            row_counts[table_name] = len(table_data) if isinstance(table_data, (dict, list)) else 0
        elif getattr(engine, "verified_row_counts", None) is not None:
            row_counts[table_name] = engine.verified_row_counts[table_name]
        elif engine is not None and hasattr(engine, "connect"):
            quoted_table = f"`{table_name.replace('`', '``')}`"
            with engine.connect() as conn:
                count = conn.execute(sa.text(f"SELECT COUNT(*) FROM {quoted_table}")).scalar()
            row_counts[table_name] = int(count or 0)
        else:
            row_counts[table_name] = max(0, schema.tables[table_name].row_count)

    total_rows = sum(row_counts.values())
    if total_rows == 0 or budget <= 0:
        return {table_name: [] for table_name in schema.table_names}

    pool_size = min(
        total_rows,
        max(budget, math.ceil(budget * CANDIDATE_POOL_MULTIPLIER)),
    )
    candidate_rows: dict[str, list[Any]] = {}
    for table_name in schema.table_names:
        table_rows = row_counts[table_name]
        if table_rows == 0:
            candidate_rows[table_name] = []
            continue
        sample_size = min(
            table_rows,
            max(1, math.ceil(pool_size * table_rows / total_rows)),
        )
        candidate_rows[table_name] = _sample_primary_keys(
            schema,
            table_name,
            engine,
            sample_size,
            rng,
        )

    return candidate_rows


def _candidate_closure(
    graph: DependencyGraph,
    engine: Engine | dict[str, Any] | None,
    table_name: str,
    pk_value: Any,
    selected: dict[str, set[Any]] | None = None,
    remaining: int | None = None,
) -> dict[str, set[Any]]:
    """Compute the dependency closure for one candidate, independent of selected rows."""
    if not graph.schema.tables[table_name].foreign_keys:
        return {table_name: {pk_value} - (selected or {}).get(table_name, set())}
    closure = compute_closure(
        graph,
        {table_name: {pk_value}},
        engine=engine,
        satisfied=selected,
        stop_after=remaining,
    )
    return closure.selected_rows


def _incremental_row_cost(
    selected: dict[str, set[Any]],
    candidate_closure: dict[str, set[Any]],
) -> int:
    """Count rows in a candidate closure that are not already selected."""
    return sum(
        len(rows - selected.get(table_name, set()))
        for table_name, rows in candidate_closure.items()
    )


def _merge_closure(
    selected: dict[str, set[Any]],
    candidate_closure: dict[str, set[Any]],
) -> None:
    """Merge a candidate's already-computed transitive closure into the selection."""
    for table_name, rows in candidate_closure.items():
        selected.setdefault(table_name, set()).update(rows)


def _record_decision(selection_log: list[str], message: str) -> None:
    """Bound the in-memory decision log for large selections."""
    if len(selection_log) < SELECTION_LOG_LIMIT:
        selection_log.append(message)


def select_rows(
    schema: SchemaMetadata,
    workload: WorkloadAnalysis | None = None,
    graph: DependencyGraph | None = None,
    engine: Engine | dict[str, Any] | None = None,
    budget: int = 0,
    method: str = "random-repair",
    seed: int = 42,
    *,
    priority_queries: tuple[str, ...] = (),
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
    priority_queries : tuple[str, ...]
        Workload query IDs to visit first in each witness round.

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
        raise ValueError(f"Unknown method '{method}'. Supported methods: {', '.join(METHODS)}")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ValueError("Budget must be a non-negative integer.")
    missing_keys = [
        name for name, table in schema.tables.items() if not table.effective_primary_keys
    ]
    if missing_keys:
        raise ValueError(
            "Selection requires a primary key on every table: " + ", ".join(missing_keys)
        )

    composite_fk_features = [
        feature
        for feature in schema.unsupported_features
        if "composite foreign key" in feature.lower()
    ]
    if composite_fk_features:
        raise ValueError(
            "Selection does not support composite foreign keys: " + "; ".join(composite_fk_features)
        )

    graph = _ensure_graph(schema, graph)
    clear_fk_cache()
    try:
        if method == "random-repair":
            return _select_random_repair(schema, graph, engine, budget, seed)
        elif method == "referential":
            return _select_referential(schema, graph, engine, budget, seed)
        return _select_workload_aware(
            schema,
            workload,
            graph,
            engine,
            budget,
            seed,
            priority_queries,
        )
    finally:
        clear_fk_cache()


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
    selected_total = 0
    selection_log: list[str] = [f"Random repair baseline with seed={seed}"]
    rejected_candidates = 0
    total_closure_additions = 0

    candidate_rows = _candidate_rows_by_table(schema, engine, budget, seed)
    prime_foreign_key_cache(graph, engine, candidate_rows)
    candidate_pairs = [
        (table_name, pk_value)
        for table_name in schema.table_names
        for pk_value in candidate_rows[table_name]
    ]

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

    for candidate_index, (table_name, pk_value) in enumerate(candidate_pairs, start=1):
        checkpoint()
        if pk_value in selected.get(table_name, set()):
            continue
        closure_rows = _candidate_closure(
            graph, engine, table_name, pk_value, selected, budget - selected_total
        )
        additional_rows = _incremental_row_cost(selected, closure_rows)
        if selected_total + additional_rows <= budget:
            _merge_closure(selected, closure_rows)
            selected_total += additional_rows
            total_closure_additions += max(0, additional_rows - 1)
            _record_decision(
                selection_log,
                f"Accepted {table_name}.{pk_value}: +{additional_rows} rows, "
                f"total={selected_total}",
            )
            if selected_total >= budget:
                break
        else:
            rejected_candidates += 1
            _record_decision(
                selection_log,
                f"Rejected {table_name}.{pk_value}: closure exceeds budget",
            )
        if candidate_index % 100_000 == 0:
            logger.info(
                "Random-repair evaluated %d/%d candidates; selected %d/%d budget rows",
                candidate_index,
                len(candidate_pairs),
                selected_total,
                budget,
            )

    return SelectionResult(
        method="random-repair",
        seed=seed,
        budget=budget,
        selected_rows={
            table: _stable_pk_values(list(values)) for table, values in selected.items() if values
        },
        total_selected=selected_total,
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
    selected_total = 0
    selection_log: list[str] = [f"Referential baseline with seed={seed}"]
    rejected_candidates = 0
    total_closure_additions = 0
    rng = random.Random(seed)
    candidate_rows = _candidate_rows_by_table(schema, engine, budget, seed)
    prime_foreign_key_cache(graph, engine, candidate_rows)

    for table_name in table_order:
        pk_values = candidate_rows.get(table_name, [])
        rng.shuffle(pk_values)
        for candidate_index, pk_value in enumerate(pk_values, start=1):
            checkpoint()
            if pk_value in selected.get(table_name, set()):
                continue
            closure_rows = _candidate_closure(
                graph, engine, table_name, pk_value, selected, budget - selected_total
            )
            additional_rows = _incremental_row_cost(selected, closure_rows)
            if selected_total + additional_rows <= budget:
                _merge_closure(selected, closure_rows)
                selected_total += additional_rows
                total_closure_additions += max(0, additional_rows - 1)
                _record_decision(
                    selection_log,
                    f"Added {table_name}.{pk_value}: +{additional_rows} rows, "
                    f"total={selected_total}",
                )
                if selected_total >= budget:
                    break
            else:
                rejected_candidates += 1
                _record_decision(
                    selection_log,
                    f"Skipped {table_name}.{pk_value}: closure exceeds budget",
                )
            if candidate_index % 100_000 == 0:
                logger.info(
                    "Referential selection evaluated %d candidates in '%s'; "
                    "selected %d/%d budget rows",
                    candidate_index,
                    table_name,
                    selected_total,
                    budget,
                )

        if selected_total >= budget:
            break

    return SelectionResult(
        method="referential",
        seed=seed,
        budget=budget,
        selected_rows={
            table: _stable_pk_values(list(values)) for table, values in selected.items() if values
        },
        total_selected=selected_total,
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
    priority_queries: tuple[str, ...] = (),
) -> SelectionResult:
    """
    Proposed method: SafeSliceSQL workload-aware greedy selection.

    Seed actual query witnesses, then fill by table priority and incremental
    dependency closure. No quadratic standalone-closure scoring pass.
    """
    graph = _ensure_graph(schema, graph)
    selected: dict[str, set[Any]] = {table: set() for table in schema.table_names}
    selected_total = 0
    selection_log: list[str] = [f"Workload-aware selector with seed={seed}"]

    require_usable_workload(workload, for_selection=True)
    if budget == 0:
        return SelectionResult("workload-aware", seed, budget, {}, selection_log=selection_log)

    table_weights: dict[str, float] = {}
    for query in workload.queries:
        if not query.is_supported or not query.tables or query.analysis_error:
            continue
        for table_name in query.tables:
            table_weights[table_name] = table_weights.get(table_name, 0.0) + 2.0
        if query.join_tables:
            table_weights[query.tables[0]] = table_weights.get(query.tables[0], 0.0) + 0.5 * len(
                query.join_tables
            )
        if query.filter_columns:
            for table_name, _ in query.filter_columns:
                if table_name in schema.tables:
                    table_weights[table_name] = table_weights.get(table_name, 0.0) + 1.0
        if query.group_columns or query.aggregate_functions:
            for table_name, _ in query.group_columns:
                if table_name in schema.tables:
                    table_weights[table_name] = table_weights.get(table_name, 0.0) + 1.5
            table_weights[query.tables[0]] = table_weights.get(query.tables[0], 0.0) + 1.0

    witnesses = []
    usable = [q for q in workload.queries if q.is_supported and not q.analysis_error]
    if priority_queries:
        priority = set(priority_queries)
        usable.sort(key=lambda query: query.query_id not in priority)
    for query in usable:
        witnesses.append(
            query_witnesses(query, schema, engine, min(1000, max(1, budget // max(1, len(usable)))))
        )
    # Give every declared query an opportunity before consuming its remaining witnesses.
    total_closure_additions = 0
    rejected_candidates = 0
    witness_order = (
        rows[offset]
        for offset in range(max((len(rows) for rows in witnesses), default=0))
        for rows in witnesses
        if offset < len(rows)
    )
    while selected_total < budget:
        batch = list(islice(witness_order, WITNESS_PREFETCH_SIZE))
        if not batch:
            break
        if hasattr(engine, "connect"):
            pending: dict[str, set[Any]] = {}
            for witness in batch:
                for table_name, keys in witness.items():
                    pending.setdefault(table_name, set()).update(keys - selected[table_name])
            prime_foreign_key_cache(
                graph, engine, {name: list(keys) for name, keys in pending.items()}
            )
        for witness in batch:
            if selected_total >= budget:
                break
            closure = compute_closure(
                graph,
                witness,
                engine=engine,
                satisfied=selected,
                stop_after=budget - selected_total,
            )
            cost = _incremental_row_cost(selected, closure.selected_rows)
            if selected_total + cost <= budget:
                _merge_closure(selected, closure.selected_rows)
                selected_total += cost
                total_closure_additions += max(0, cost - sum(map(len, witness.values())))
                _record_decision(selection_log, f"Accepted query witness: +{cost} rows")
            else:
                rejected_candidates += 1

    candidate_rows = _candidate_rows_by_table(
        schema, engine, budget if selected_total < budget else 0, seed
    )
    prime_foreign_key_cache(graph, engine, candidate_rows)
    rng = random.Random(seed)
    scored_candidates: list[tuple[float, str, Any]] = []
    for table_name, pk_values in candidate_rows.items():
        workload_value = table_weights.get(table_name, 1.0)
        for candidate_index, pk_value in enumerate(pk_values, start=1):
            checkpoint()
            score = workload_value
            scored_candidates.append((score, table_name, pk_value))
            if candidate_index % 100_000 == 0:
                logger.info(
                    "Workload-aware scored %d candidates in '%s'",
                    candidate_index,
                    table_name,
                )
    rng.shuffle(scored_candidates)
    priority_groups: dict[float, list[tuple[float, str, Any]]] = {}
    for candidate in scored_candidates:
        priority_groups.setdefault(candidate[0], []).append(candidate)
    candidate_count = len(scored_candidates)
    scored_candidates.clear()
    candidate_rows.clear()

    # Table priority fills unused space after witnesses; charge only new dependencies.
    for candidate_index, (score, table_name, pk_value) in enumerate(
        (
            candidate
            for weight in sorted(priority_groups, reverse=True)
            for candidate in priority_groups[weight]
        ),
        start=1,
    ):
        checkpoint()
        if selected_total >= budget:
            break
        if pk_value in selected.get(table_name, set()):
            continue
        closure_rows = _candidate_closure(
            graph, engine, table_name, pk_value, selected, budget - selected_total
        )
        additional_rows = _incremental_row_cost(selected, closure_rows)
        if additional_rows == 0:
            continue
        if selected_total + additional_rows <= budget:
            _merge_closure(selected, closure_rows)
            selected_total += additional_rows
            total_closure_additions += max(0, additional_rows - 1)
            _record_decision(
                selection_log,
                f"Selected {table_name}.{pk_value}: score={score:.3f}, "
                f"added={additional_rows}, total_rows={selected_total}",
            )
        else:
            rejected_candidates += 1
            _record_decision(
                selection_log,
                f"Skipped {table_name}.{pk_value}: closure exceeds budget",
            )
        if candidate_index % 100_000 == 0:
            logger.info(
                "Workload-aware evaluated %d/%d scored candidates; selected %d/%d budget rows",
                candidate_index,
                candidate_count,
                selected_total,
                budget,
            )

    return SelectionResult(
        method="workload-aware",
        seed=seed,
        budget=budget,
        selected_rows={
            table: _stable_pk_values(list(values)) for table, values in selected.items() if values
        },
        total_selected=selected_total,
        closure_additions=total_closure_additions,
        rejected_candidates=rejected_candidates,
        selection_log=selection_log,
    )
