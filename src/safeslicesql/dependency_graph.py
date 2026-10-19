"""
safeslicesql.dependency_graph — Foreign-key dependency graph.

Builds a directed graph of foreign-key relationships using
parent → child edges. Computes the transitive dependency closure
for a given set of selected rows: if a child row is selected,
all required parent rows are recursively included.

Detects cycles and unsupported graph structures.

Owner: M1
"""

from __future__ import annotations

import logging
from typing import Any

from safeslicesql.types import ClosureResult, SchemaMetadata

logger = logging.getLogger("safeslicesql.dependency_graph")


class DependencyGraph:
    """
    A directed graph of foreign-key dependencies.

    Edges go from referenced (parent) table to referencing (child) table.
    Given selected child rows, the graph is used to find all required
    parent rows via transitive closure.
    """

    def __init__(self, schema: SchemaMetadata) -> None:
        raise NotImplementedError("Phase 2: implement graph construction")

    @property
    def has_cycles(self) -> bool:
        """Return True if the FK graph contains cycles."""
        raise NotImplementedError("Phase 2: implement cycle detection")

    def get_parents(self, table_name: str) -> list[str]:
        """Return parent (referenced) tables for the given table."""
        raise NotImplementedError("Phase 2: implement parent lookup")

    def get_children(self, table_name: str) -> list[str]:
        """Return child (referencing) tables for the given table."""
        raise NotImplementedError("Phase 2: implement child lookup")

    def topological_order(self) -> list[str]:
        """
        Return tables in topological (parent-first) order.

        Used for determining safe insertion order during materialization.

        Raises
        ------
        ValueError
            If the graph contains cycles.
        """
        raise NotImplementedError("Phase 2: implement topological sort")


def build_dependency_graph(schema: SchemaMetadata) -> DependencyGraph:
    """
    Build a foreign-key dependency graph from schema metadata.

    Parameters
    ----------
    schema : SchemaMetadata
        Schema metadata from the schema analyzer.

    Returns
    -------
    DependencyGraph
        A dependency graph with parent → child edges.
    """
    raise NotImplementedError("Phase 2: implement graph building")


def compute_closure(
    graph: DependencyGraph,
    selected: dict[str, set[Any]],
    engine: Any,
    budget: int | None = None,
) -> ClosureResult:
    """
    Compute the transitive dependency closure for selected rows.

    For each selected child row, find all referenced parent rows
    that are not yet in the selection. Add them recursively until
    no further dependencies are needed.

    Parameters
    ----------
    graph : DependencyGraph
        The FK dependency graph.
    selected : dict[str, set[Any]]
        Currently selected rows: table_name → set of PK values.
    engine : sqlalchemy.Engine
        Engine to query FK values from the source database.
    budget : int or None
        If provided, the hard row budget. Return infeasible if
        the closure would exceed this limit.

    Returns
    -------
    ClosureResult
        The expanded selection including all required parent rows.
    """
    raise NotImplementedError("Phase 2: implement dependency closure")
