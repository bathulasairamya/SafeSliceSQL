"""
safeslicesql.subset_generator — Target database creation and population.

Creates a fresh target schema, reproduces supported table definitions,
and inserts selected rows in safe dependency order (parents before
children) while preserving original primary-key values.

Uses transactions for safety. Does NOT disable foreign-key checks.

Owner: M1
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine

from safeslicesql.dependency_graph import DependencyGraph
from safeslicesql.types import GenerationResult, SchemaMetadata, SelectionResult

logger = logging.getLogger("safeslicesql.subset_generator")


def materialize_subset(
    source_engine: Engine,
    target_engine: Engine,
    schema: SchemaMetadata,
    selection: SelectionResult,
    graph: DependencyGraph,
    target_database: str,
) -> GenerationResult:
    """
    Create and populate the target database with selected rows.

    Steps:
    1. Create or refresh the target database.
    2. Reproduce supported table definitions from source schema.
    3. Determine safe insertion order via topological sort.
    4. Insert selected rows table-by-table, parents first.
    5. Preserve original primary-key values.
    6. Use transactions for atomicity.

    Parameters
    ----------
    source_engine : sqlalchemy.Engine
        Engine connected to the source database.
    target_engine : sqlalchemy.Engine
        Engine connected to the target database server.
    schema : SchemaMetadata
        Source schema metadata.
    selection : SelectionResult
        Rows to insert, organized by table.
    graph : DependencyGraph
        Dependency graph for determining insertion order.
    target_database : str
        Name of the target database to create/populate.

    Returns
    -------
    GenerationResult
        Summary of the materialization, including success status.
    """
    raise NotImplementedError("Phase 3: implement subset materialization")
