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

from sqlalchemy import Engine

from safeslicesql.dependency_graph import DependencyGraph
from safeslicesql.types import SchemaMetadata, SelectionResult
from safeslicesql.workload_analyzer import WorkloadAnalysis

logger = logging.getLogger("safeslicesql.sampler")

# Valid method identifiers
METHODS = ("random-repair", "referential", "workload-aware")


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
    graph: DependencyGraph,
    engine: Engine,
    budget: int,
    seed: int,
) -> SelectionResult:
    """
    Baseline A: Random sampling with dependency repair.

    1. Select seed rows randomly using the recorded seed.
    2. Compute dependency closure.
    3. Reject infeasible candidates under the hard budget.
    """
    raise NotImplementedError("Phase 4: implement random sampling with repair")


def _select_referential(
    schema: SchemaMetadata,
    graph: DependencyGraph,
    engine: Engine,
    budget: int,
    seed: int,
) -> SelectionResult:
    """
    Baseline B: Referential/table-aware sampling.

    1. Use a documented table-aware seed-selection policy.
    2. Compute dependency closure.
    3. Enforce the same hard budget.
    """
    raise NotImplementedError("Phase 4: implement referential sampling")


def _select_workload_aware(
    schema: SchemaMetadata,
    workload: WorkloadAnalysis,
    graph: DependencyGraph,
    engine: Engine,
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
    raise NotImplementedError("Phase 5: implement workload-aware selection")
