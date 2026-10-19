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
    def self_referential_tables(self) -> list[str]:
        """Return table names that contain a self-referencing foreign key."""
        return sorted([u for u, v in self._graph.edges() if u == v])

    @property
    def has_cycles(self) -> bool:
        """
        Return True if the FK graph contains cross-table cycles.
        Self-referential relationships are tracked separately and do not block DAG sorting.
        """
        clean_graph = self._graph.copy()
        clean_graph.remove_edges_from(nx.selfloop_edges(clean_graph))
        return not nx.is_directed_acyclic_graph(clean_graph)

    def cycles(self) -> list[list[str]]:
        """Return all simple directed cross-table cycles found in the graph."""
        clean_graph = self._graph.copy()
        clean_graph.remove_edges_from(nx.selfloop_edges(clean_graph))
        if nx.is_directed_acyclic_graph(clean_graph):
            return []
        return list(nx.simple_cycles(clean_graph))

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
        Self-referential relationships are excluded from table-level ordering.

        Raises
        ------
        ValueError
            If the graph contains cross-table cycles.
        """
        clean_graph = self._graph.copy()
        clean_graph.remove_edges_from(nx.selfloop_edges(clean_graph))
        if not nx.is_directed_acyclic_graph(clean_graph):
            cycles_str = ", ".join(" -> ".join(c) for c in nx.simple_cycles(clean_graph))
            raise ValueError(
                f"Cannot compute topological order: foreign-key graph contains cycles: {cycles_str}"
            )
        return list(nx.topological_sort(clean_graph))


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


def _quote_ident(name: str) -> str:
    """Safely quote a SQL identifier using backticks."""
    return f"`{name.replace('`', '``')}`"


def _resolve_fks_batch(
    engine: Any,
    table_name: str,
    pk_columns: list[str],
    pk_values: list[Any],
    fk_columns: list[str],
) -> dict[Any, dict[str, Any]]:
    """
    Batch retrieve foreign-key column values for a set of primary keys.

    Solves the N+1 database query storm by retrieving all foreign key
    columns for multiple primary keys in a single query.

    Parameters
    ----------
    engine : Any
        Live SQLAlchemy engine/connection, in-memory dictionary, or custom callable.
    table_name : str
        Target table name.
    pk_columns : list[str]
        Primary key column names.
    pk_values : list[Any]
        List of primary key values.
    fk_columns : list[str]
        Foreign key column names to fetch.

    Returns
    -------
    dict[Any, dict[str, Any]]
        Mapping of pk_value -> {fk_column: fk_value}
    """
    if not pk_values or not fk_columns or engine is None:
        return {pk: {fk: None for fk in fk_columns} for pk in pk_values}

    results: dict[Any, dict[str, Any]] = {}

    # 1. Custom callable
    if isinstance(engine, Callable):
        for pk in pk_values:
            row_fks: dict[str, Any] = {}
            for fk in fk_columns:
                row_fks[fk] = engine(table_name, pk, fk)
            results[pk] = row_fks
        return results

    # 2. In-memory dictionary store
    if isinstance(engine, dict):
        table_data = engine.get(table_name)
        if isinstance(table_data, dict):
            for pk in pk_values:
                row = table_data.get(pk)
                if isinstance(row, dict):
                    results[pk] = {fk: row.get(fk) for fk in fk_columns}
                else:
                    results[pk] = {fk: None for fk in fk_columns}
            return results
        elif isinstance(table_data, list):
            # Build index in O(N) once to avoid O(M*N) linear scanning
            pk_col = pk_columns[0] if pk_columns else "id"
            indexed: dict[Any, dict[str, Any]] = {}
            for row in table_data:
                if isinstance(row, dict):
                    key = row.get(pk_col)
                    if key is not None:
                        indexed[key] = row
            for pk in pk_values:
                row = indexed.get(pk)
                if row:
                    results[pk] = {fk: row.get(fk) for fk in fk_columns}
                else:
                    results[pk] = {fk: None for fk in fk_columns}
            return results
        return {pk: {fk: None for fk in fk_columns} for pk in pk_values}

    # 3. SQLAlchemy Engine or Connection
    if hasattr(engine, "connect"):
        primary_key_col = pk_columns[0] if pk_columns else "id"
        quoted_table = _quote_ident(table_name)
        quoted_pk = _quote_ident(primary_key_col)
        quoted_fks = ", ".join(_quote_ident(fk) for fk in fk_columns)

        # Chunk large lists of PKs (500 per query) to avoid parameter limits
        chunk_size = 500
        try:
            with engine.connect() as conn:
                for i in range(0, len(pk_values), chunk_size):
                    batch = pk_values[i : i + chunk_size]
                    param_names = [f"pk_{j}" for j in range(len(batch))]
                    in_clause = ", ".join(f":{p}" for p in param_names)
                    stmt = sa.text(
                        f"SELECT {quoted_pk}, {quoted_fks} FROM {quoted_table} WHERE {quoted_pk} IN ({in_clause})"
                    )
                    params = dict(zip(param_names, batch))
                    exec_res = conn.execute(stmt, params)
                    for row in exec_res.mappings():
                        r_pk = row[primary_key_col]
                        results[r_pk] = {fk: row[fk] for fk in fk_columns}
        except Exception as exc:
            logger.error(
                "Database error while batch querying FKs on table '%s': %s",
                table_name, exc,
            )
            from safeslicesql.types import DatabaseResolutionError
            raise DatabaseResolutionError(
                f"Failed to query foreign keys on table '{table_name}': {exc}"
            ) from exc

        # Fill in missing PKs with None
        for pk in pk_values:
            if pk not in results:
                results[pk] = {fk: None for fk in fk_columns}

        return results

    return {pk: {fk: None for fk in fk_columns} for pk in pk_values}


def _resolve_fk_value(
    engine: Any,
    table_name: str,
    pk_column: str,
    pk_value: Any,
    fk_column: str,
) -> Any:
    """
    Retrieve the foreign-key column value for a given primary key.
    Provides single-row resolution compatibility via _resolve_fks_batch.
    """
    batch_res = _resolve_fks_batch(
        engine=engine,
        table_name=table_name,
        pk_columns=[pk_column],
        pk_values=[pk_value],
        fk_columns=[fk_column],
    )
    return batch_res.get(pk_value, {}).get(fk_column)


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

    Uses high-performance wave-based batch resolution to prevent
    N+1 database queries.

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

    # Track inspected rows: table -> set of PKs whose outgoing FKs have been resolved
    inspected_rows: dict[str, set[Any]] = {
        table_name: set() for table_name in graph.schema.tables
    }

    while True:
        # Collect all rows across tables that need FK inspection
        uninspected_by_table: dict[str, list[Any]] = {}
        for table_name, pks in current_selection.items():
            to_inspect = list(pks - inspected_rows[table_name])
            if to_inspect:
                uninspected_by_table[table_name] = to_inspect

        if not uninspected_by_table:
            break

        # Process each table's uninspected batch
        for child_table, pks_to_inspect in uninspected_by_table.items():
            child_meta = graph.schema.tables.get(child_table)
            if child_meta is None or not child_meta.foreign_keys:
                inspected_rows[child_table].update(pks_to_inspect)
                continue

            pk_cols = child_meta.effective_primary_keys or ["id"]
            fk_cols = [fk.column for fk in child_meta.foreign_keys]

            # Batch resolve all FKs for these PKs in a single query/operation
            batch_results = _resolve_fks_batch(
                engine=engine,
                table_name=child_table,
                pk_columns=pk_cols,
                pk_values=pks_to_inspect,
                fk_columns=fk_cols,
            )

            # Map resolved parent keys into parent tables
            for fk in child_meta.foreign_keys:
                parent_table = fk.referenced_table
                child_col = fk.column
                current_parent_pks = current_selection.setdefault(parent_table, set())

                for child_pk in pks_to_inspect:
                    fk_value = batch_results.get(child_pk, {}).get(child_col)

                    # v1 Nullable Foreign Key Policy: NULL = no dependency
                    if fk_value is None:
                        continue

                    if fk_value not in current_parent_pks:
                        current_parent_pks.add(fk_value)
                        closure_additions.setdefault(parent_table, set()).add(fk_value)

            inspected_rows[child_table].update(pks_to_inspect)

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
