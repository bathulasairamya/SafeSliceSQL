"""
safeslicesql.dependency_graph — Foreign-key dependency graph.

Builds a directed graph of foreign-key relationships using
parent → child edges. Computes the transitive dependency closure
for a given set of selected rows: if a child row is selected,
all required parent rows are recursively included.

Detects cycles and unsupported graph structures.
Supports the v1 nullable foreign-key policy: NULL = no dependency.

Owner: M1
"""

from __future__ import annotations

import collections
import logging
from typing import Any, Callable

import networkx as nx
import sqlalchemy as sa

from safeslicesql.types import (
    ClosureResult,
    ForeignKeyMetadata,
    SchemaMetadata,
)

logger = logging.getLogger("safeslicesql.dependency_graph")


class DependencyGraph:
    """
    A directed graph of foreign-key dependencies.

    Edges go from referenced (parent) table to referencing (child) table.
    Given selected child rows, the graph is used to find all required
    parent rows via transitive closure.
    """

    def __init__(self, schema: SchemaMetadata) -> None:
        self.schema = schema
        self._graph = nx.DiGraph()

        # Add all tables as nodes
        for table_name in schema.tables:
            self._graph.add_node(table_name)

        # Add edges: parent -> child
        for child_table, table_meta in schema.tables.items():
            for fk in table_meta.foreign_keys:
                parent_table = fk.referenced_table
                if not self._graph.has_node(parent_table):
                    self._graph.add_node(parent_table)

                if self._graph.has_edge(parent_table, child_table):
                    self._graph[parent_table][child_table]["fks"].append(fk)
                else:
                    self._graph.add_edge(parent_table, child_table, fks=[fk])

        logger.debug(
            "Built dependency graph with %d nodes and %d edges",
            self._graph.number_of_nodes(),
            self._graph.number_of_edges(),
        )

    @property
    def has_cycles(self) -> bool:
        """Return True if the FK graph contains cycles."""
        return not nx.is_directed_acyclic_graph(self._graph)

    def cycles(self) -> list[list[str]]:
        """Return all simple directed cycles found in the graph."""
        if nx.is_directed_acyclic_graph(self._graph):
            return []
        return list(nx.simple_cycles(self._graph))

    def get_parents(self, table_name: str) -> list[str]:
        """
        Return parent (referenced) tables for the given table.

        In the parent → child representation, parents are predecessors.
        """
        if not self._graph.has_node(table_name):
            return []
        return sorted(self._graph.predecessors(table_name))

    def get_children(self, table_name: str) -> list[str]:
        """
        Return child (referencing) tables for the given table.

        In the parent → child representation, children are successors.
        """
        if not self._graph.has_node(table_name):
            return []
        return sorted(self._graph.successors(table_name))

    def get_foreign_keys_to_parent(
        self, child_table: str, parent_table: str
    ) -> list[ForeignKeyMetadata]:
        """Return foreign keys on child_table pointing to parent_table."""
        if self._graph.has_edge(parent_table, child_table):
            return list(self._graph[parent_table][child_table].get("fks", []))
        return []

    def topological_order(self) -> list[str]:
        """
        Return tables in topological (parent-first) order.

        Used for determining safe insertion order during materialization.

        Raises
        ------
        ValueError
            If the graph contains cycles.
        """
        if self.has_cycles:
            cycles_str = ", ".join(" -> ".join(c) for c in self.cycles())
            raise ValueError(
                f"Cannot compute topological order: foreign-key graph contains cycles: {cycles_str}"
            )
        return list(nx.topological_sort(self._graph))


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
    return DependencyGraph(schema)


def compute_closure(
    graph: DependencyGraph,
    selected: dict[str, set[Any]],
    engine: Any = None,
    budget: int | None = None,
) -> ClosureResult:
    """
    Compute the transitive dependency closure for selected rows.

    For each selected child row, find all referenced parent rows
    that are not yet in the selection. Add them recursively until
    no further dependencies are needed.

    Supports the v1 nullable foreign-key policy:
    NULL = no dependency. If a foreign-key column is NULL, no parent
    row is required.

    Parameters
    ----------
    graph : DependencyGraph
        The FK dependency graph.
    selected : dict[str, set[Any]]
        Currently selected rows: table_name → set of PK values.
    engine : Any
        Live SQLAlchemy engine/connection, in-memory dictionary
        `{table: {pk: {col: val}}}`, or custom row-fetcher callable.
        If None, closure only accounts for already known references.
    budget : int or None
        If provided, the hard row budget. Return infeasible if
        the closure would exceed this limit.

    Returns
    -------
    ClosureResult
        The expanded selection including all required parent rows.
    """
    current_selection: dict[str, set[Any]] = {
        table: set(pks) for table, pks in selected.items()
    }
    for table_name in graph.schema.tables:
        current_selection.setdefault(table_name, set())

    closure_additions: dict[str, set[Any]] = {
        table_name: set() for table_name in graph.schema.tables
    }

    # Queue of (table_name, pk_val) rows whose outgoing FKs must be inspected
    queue: collections.deque[tuple[str, Any]] = collections.deque()
    inspected_rows: set[tuple[str, Any]] = set()

    for table_name, pks in current_selection.items():
        for pk in pks:
            queue.append((table_name, pk))

    while queue:
        child_table, child_pk = queue.popleft()
        row_id = (child_table, child_pk)
        if row_id in inspected_rows:
            continue
        inspected_rows.add(row_id)

        child_meta = graph.schema.tables.get(child_table)
        if child_meta is None or not child_meta.foreign_keys:
            continue

        for fk in child_meta.foreign_keys:
            parent_table = fk.referenced_table
            child_col = fk.column
            parent_pk_col = fk.referenced_column

            # Resolve the foreign key value for this child row
            fk_value = _resolve_fk_value(
                engine=engine,
                table_name=child_table,
                pk_column=child_meta.primary_key or "id",
                pk_value=child_pk,
                fk_column=child_col,
            )

            # v1 Nullable Foreign Key Policy: NULL = no dependency
            if fk_value is None:
                continue

            current_parent_pks = current_selection.setdefault(parent_table, set())
            if fk_value not in current_parent_pks:
                current_parent_pks.add(fk_value)
                closure_additions.setdefault(parent_table, set()).add(fk_value)
                queue.append((parent_table, fk_value))

    total_rows = sum(len(pks) for pks in current_selection.values())
    total_additions = sum(len(pks) for pks in closure_additions.values())

    is_feasible = True
    infeasibility_reason = ""
    if budget is not None and total_rows > budget:
        is_feasible = False
        infeasibility_reason = (
            f"Closure size ({total_rows} rows) exceeds hard budget of {budget} rows"
        )
        logger.debug(
            "Closure infeasible: budget=%d, total_rows=%d, additions=%d",
            budget, total_rows, total_additions,
        )

    return ClosureResult(
        selected_rows=current_selection,
        closure_additions=closure_additions,
        total_rows=total_rows,
        total_closure_additions=total_additions,
        is_feasible=is_feasible,
        infeasibility_reason=infeasibility_reason,
    )


def _resolve_fk_value(
    engine: Any,
    table_name: str,
    pk_column: str,
    pk_value: Any,
    fk_column: str,
) -> Any:
    """
    Retrieve the foreign-key column value for a given primary key.

    Supports:
    1. Live SQLAlchemy Engine / Connection
    2. In-memory dictionary store: {table: {pk_val: {col: val}}} or {table: [{col: val}]}
    3. Custom callable: engine(table_name, pk_value, fk_column) -> Any
    """
    if engine is None:
        return None

    # Callable resolver
    if isinstance(engine, Callable):
        return engine(table_name, pk_value, fk_column)

    # In-memory dictionary store
    if isinstance(engine, dict):
        table_data = engine.get(table_name)
        if isinstance(table_data, dict):
            row = table_data.get(pk_value)
            if isinstance(row, dict):
                return row.get(fk_column)
        elif isinstance(table_data, list):
            for row in table_data:
                if isinstance(row, dict) and row.get(pk_column) == pk_value:
                    return row.get(fk_column)
        return None

    # SQLAlchemy Engine or Connection
    if hasattr(engine, "connect"):
        try:
            with engine.connect() as conn:
                stmt = sa.text(
                    f"SELECT `{fk_column}` FROM `{table_name}` WHERE `{pk_column}` = :pk"
                )
                result = conn.execute(stmt, {"pk": pk_value})
                return result.scalar()
        except Exception as exc:
            logger.warning(
                "Failed to query FK '%s' on '%s' for PK %s: %s",
                fk_column, table_name, pk_value, exc,
            )
            return None

    return None
