"""
safeslicesql.subset_generator — Target database creation and population.

Creates missing target tables from reflected definitions and refreshes
compatible existing targets transactionally, preserving primary-key values.

Uses transactions for safety and atomicity. Does NOT disable foreign-key checks.
Enforces hard row budget limits and prevents source database overwrites.

Owner: M1
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

import networkx as nx
import sqlalchemy as sa
from sqlalchemy import Engine

from safeslicesql.db import assert_distinct_databases, quote_ident
from safeslicesql.dependency_graph import DependencyGraph
from safeslicesql.schema_compatibility import table_schema_differences
from safeslicesql.types import (
    GenerationResult,
    SchemaMetadata,
    SelectionResult,
    TableMetadata,
)

logger = logging.getLogger("safeslicesql.subset_generator")


def materialize_subset(
    source_engine: Engine,
    target_engine: Engine,
    schema: SchemaMetadata,
    selection: SelectionResult,
    graph: DependencyGraph,
    target_database: str,
    chunk_size: int = 500,
) -> GenerationResult:
    """
    Create and populate the target database with selected rows.

    Steps:
    1. Validate safety: target database must differ from source database.
    2. Check budget compliance: total selected rows must not exceed budget.
    3. Require distinct, accessible source and target databases.
    4. Determine safe insertion order via topological sort (parents first).
    5. Reflect exact definitions and reject incompatible existing target tables.
    6. Refresh target rows with transactional deletes and parent-first inserts.
    7. Preserve original primary-key values without disabling foreign-key checks.

    Parameters
    ----------
    source_engine : sqlalchemy.Engine
        Engine connected to the source database (read-only).
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
    chunk_size : int
        Batch size for copying rows.

    Returns
    -------
    GenerationResult
        Summary of the materialization, including success status.
    """
    logger.info(
        "Starting subset materialization into target database '%s'",
        target_database,
    )

    # Check the connected databases, never a caller-supplied display name.
    assert_distinct_databases(source_engine, target_engine, target_database)

    # Reject invalid identities/budgets before any target schema or data changes.
    try:
        selected_rows = _validate_selection(schema, selection, chunk_size)
    except ValueError as exc:
        return GenerationResult(target_database=target_database, error_message=str(exc))
    total_selected = sum(len(keys) for keys in selected_rows.values())

    # 3. Determine safe insertion order via topological sort
    if graph.has_cycles:
        err_msg = (
            f"Cannot materialize subset: foreign-key graph contains cycles: {graph.cycles()}. "
            "v1 supports acyclic foreign-key graphs only."
        )
        logger.error(err_msg)
        return GenerationResult(
            target_database=target_database,
            success=False,
            error_message=err_msg,
        )

    full_order = graph.topological_order()
    # Filter to tables known in this schema
    insertion_order = [t for t in full_order if t in schema.tables]
    # Include any isolated tables without FK relationships
    for t in schema.tables:
        if t not in insertion_order:
            insertion_order.append(t)

    logger.info("Safe insertion order (parents first): %s", insertion_order)

    # Reflect and validate before changing any existing target rows.
    created_tables: list[str] = []
    try:
        target_meta = _reproduce_schema_on_target(
            source_engine=source_engine,
            target_engine=target_engine,
            schema=schema,
            insertion_order=insertion_order,
            created_tables=created_tables,
        )
    except Exception as exc:
        err_msg = f"Failed to reproduce schema on target database: {exc}"
        logger.error(err_msg, exc_info=True)
        _cleanup_created_tables(target_engine, created_tables)
        return GenerationResult(
            target_database=target_database,
            success=False,
            error_message=err_msg,
            insertion_order=insertion_order,
        )

    # 6. Insert selected rows in topological order using an atomic transaction
    rows_inserted: dict[str, int] = {}
    try:
        with source_engine.connect() as s_conn, target_engine.begin() as t_conn:
            # DML only: rollback restores the previous subset if any copy fails.
            for tbl_name in reversed(insertion_order):
                table_meta = schema.tables[tbl_name]
                if _has_self_reference(table_meta):
                    # A bulk DELETE can visit parents before children on MySQL.
                    keys = _self_reference_order(t_conn, table_meta, None, chunk_size)
                    for key in reversed(keys):
                        condition, params = _key_condition(table_meta.effective_primary_keys, [key])
                        t_conn.execute(
                            sa.text(f"DELETE FROM {quote_ident(tbl_name)} WHERE {condition}"),
                            params,
                        )
                else:
                    t_conn.execute(sa.delete(target_meta.tables[tbl_name]))

            for tbl_name in insertion_order:
                table_meta = schema.tables[tbl_name]
                target_table = target_meta.tables[tbl_name]
                keys = selected_rows.get(tbl_name, [])
                inserted_for_table = 0
                if _has_self_reference(table_meta) and keys:
                    # Only self-referencing tables need a row-level dependency graph.
                    keys = _self_reference_order(s_conn, table_meta, keys, chunk_size)
                for start in range(0, len(keys), chunk_size):
                    batch = keys[start : start + chunk_size]
                    condition, params = _key_condition(table_meta.effective_primary_keys, batch)
                    fetched = (
                        s_conn.execute(
                            sa.text(f"SELECT * FROM {quote_ident(tbl_name)} WHERE {condition}"),
                            params,
                        )
                        .mappings()
                        .all()
                    )
                    by_key = {
                        _row_key(row, table_meta.effective_primary_keys): dict(row)
                        for row in fetched
                    }
                    if len(fetched) != len(batch) or set(by_key) != set(batch):
                        raise ValueError(
                            f"Selected rows in '{tbl_name}' are missing or not uniquely identified."
                        )
                    # SELECT IN does not guarantee order, even when the input keys are sorted.
                    payload = [_insert_values(target_table, by_key[key]) for key in batch]
                    t_conn.execute(sa.insert(target_table), payload)
                    inserted_for_table += len(payload)
                rows_inserted[tbl_name] = inserted_for_table

            if sum(rows_inserted.values()) != total_selected:
                raise ValueError("Copied row count does not match the selection.")
            # Include pre-existing unrelated target tables and trigger-created rows.
            actual_rows = sum(
                t_conn.execute(sa.text(f"SELECT COUNT(*) FROM {quote_ident(name)}")).scalar_one()
                for name in sa.inspect(t_conn).get_table_names()
            )
            if actual_rows > selection.budget:
                raise ValueError(
                    f"Target row count ({actual_rows}) exceeds hard budget limit ({selection.budget})."
                )

        total_inserted = sum(rows_inserted.values())
        logger.info(
            "Materialization succeeded: %d total rows inserted across %d tables",
            total_inserted,
            len(insertion_order),
        )

        return GenerationResult(
            target_database=target_database,
            tables_created=list(target_meta.tables.keys()),
            rows_inserted=rows_inserted,
            total_rows_inserted=total_inserted,
            insertion_order=insertion_order,
            success=True,
            error_message="",
        )

    except Exception as exc:
        err_msg = f"Materialization transaction failed: {exc}"
        logger.error(err_msg, exc_info=True)
        _cleanup_created_tables(target_engine, created_tables)
        return GenerationResult(
            target_database=target_database,
            tables_created=list(target_meta.tables.keys()) if "target_meta" in locals() else [],
            rows_inserted={},
            total_rows_inserted=0,
            insertion_order=insertion_order,
            success=False,
            error_message=err_msg,
        )


def _validate_selection(
    schema: SchemaMetadata, selection: SelectionResult, chunk_size: int
) -> dict[str, list[Any]]:
    if (
        isinstance(selection.budget, bool)
        or not isinstance(selection.budget, int)
        or selection.budget < 0
    ):
        raise ValueError("Budget must be a non-negative integer.")
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("Chunk size must be a positive integer.")
    missing_keys = [
        name for name, table in schema.tables.items() if not table.effective_primary_keys
    ]
    if missing_keys:
        raise ValueError(
            "Materialization requires a primary key on every table: " + ", ".join(missing_keys)
        )
    selected: dict[str, list[Any]] = {}
    for name, keys in selection.selected_rows.items():
        if name not in schema.tables:
            raise ValueError(f"Selection refers to unknown table '{name}'.")
        pk_columns = schema.tables[name].effective_primary_keys
        normalized = []
        for key in keys:
            if len(pk_columns) > 1:
                if (
                    not isinstance(key, (tuple, list))
                    or len(key) != len(pk_columns)
                    or any(v is None for v in key)
                ):
                    raise ValueError(f"Invalid composite primary key for '{name}'.")
                key = tuple(key)
            elif key is None or isinstance(key, (tuple, list)):
                raise ValueError(f"Invalid primary key for '{name}'.")
            normalized.append(key)
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"Duplicate selected primary keys in '{name}'.")
        selected[name] = normalized
    total = sum(len(keys) for keys in selected.values())
    if total > selection.budget:
        raise ValueError(
            f"Selection size ({total} rows) exceeds hard budget limit of {selection.budget} rows."
        )
    return selected


def _row_key(row: Any, columns: list[str]) -> Any:
    return row[columns[0]] if len(columns) == 1 else tuple(row[column] for column in columns)


def _key_condition(columns: list[str], keys: list[Any], alias: str = "") -> tuple[str, dict]:
    if len(columns) == 1:
        params = {f"pk_{index}": key for index, key in enumerate(keys)}
        names = ", ".join(f":{name}" for name in params)
        return f"{alias}{quote_ident(columns[0])} IN ({names})", params
    conditions, params = [], {}
    for index, key in enumerate(keys):
        values = (key,) if len(columns) == 1 else key
        parts = []
        for position, (column, value) in enumerate(zip(columns, values)):
            name = f"pk_{index}_{position}"
            parts.append(f"{alias}{quote_ident(column)} = :{name}")
            params[name] = value
        conditions.append("(" + " AND ".join(parts) + ")")
    return " OR ".join(conditions), params


def _has_self_reference(table: TableMetadata) -> bool:
    return any(fk.referenced_table == table.name for fk in table.foreign_keys)


def _self_reference_order(
    connection: sa.Connection, table: TableMetadata, keys: list[Any] | None, chunk_size: int
) -> list[Any]:
    """Order actual parent PKs before child PKs, using SQL's FK equality semantics."""
    pk_columns = table.effective_primary_keys
    quoted_table = quote_ident(table.name)
    if keys is None:
        result = connection.execute(
            sa.text(f"SELECT {', '.join(quote_ident(c) for c in pk_columns)} FROM {quoted_table}")
        )
        keys = [_row_key(row, pk_columns) for row in result.mappings()]
    dependencies = nx.DiGraph()
    dependencies.add_nodes_from(keys)
    for fk in table.foreign_keys:
        if fk.referenced_table != table.name:
            continue
        projection = ", ".join(
            f"{alias}.{quote_ident(c)}" for alias in ("c", "p") for c in pk_columns
        )
        for start in range(0, len(keys), chunk_size):
            condition, params = _key_condition(pk_columns, keys[start : start + chunk_size], "c.")
            statement = sa.text(
                f"SELECT {projection} FROM {quoted_table} c LEFT JOIN {quoted_table} p "
                f"ON c.{quote_ident(fk.column)} = p.{quote_ident(fk.referenced_column)} "
                f"WHERE ({condition}) AND c.{quote_ident(fk.column)} IS NOT NULL"
            )
            for row in connection.execute(statement, params):
                width = len(pk_columns)
                child = row[0] if width == 1 else tuple(row[:width])
                parent = row[width] if width == 1 else tuple(row[width:])
                if parent not in dependencies:
                    raise ValueError(f"Missing selected self-reference parent in '{table.name}'.")
                dependencies.add_edge(parent, child)
    try:
        return list(nx.topological_sort(dependencies))
    except nx.NetworkXUnfeasible as exc:
        raise ValueError(
            f"Self-referencing rows in '{table.name}' contain a cycle; cyclic rows are unsupported."
        ) from exc


def _insert_values(table: sa.Table, row: dict[str, Any]) -> dict[str, Any]:
    # Computed columns must be generated by the target, never explicitly inserted.
    values = {column.name: row[column.name] for column in table.columns if column.computed is None}
    for column in table.columns:
        value = values.get(column.name)
        if isinstance(value, str):
            if isinstance(column.type, sa.DateTime):
                values[column.name] = datetime.datetime.fromisoformat(value.replace(" ", "T"))
            elif isinstance(column.type, sa.Date):
                values[column.name] = datetime.date.fromisoformat(value)
    return values


def _cleanup_created_tables(target_engine: Engine, created_tables: list[str]) -> None:
    """Remove only tables created by this attempt; never drop pre-existing tables."""
    for table_name in reversed(created_tables):
        try:
            with target_engine.begin() as conn:
                sa.Table(table_name, sa.MetaData()).drop(conn, checkfirst=True)
        except Exception:
            logger.exception("Could not remove newly created table '%s'", table_name)


def _reproduce_schema_on_target(
    source_engine: Engine,
    target_engine: Engine,
    schema: SchemaMetadata,
    insertion_order: list[str],
    created_tables: list[str] | None = None,
) -> sa.MetaData:
    """Reflect exact definitions, refuse schema drift, and create only missing tables.

    No lossy fallback or DROP of existing tables is permitted. Existing data is
    refreshed later using transactional DELETE/INSERT operations.
    """
    assert_distinct_databases(source_engine, target_engine, target_engine.url.database or "target")
    source_meta = sa.MetaData()
    source_meta.reflect(bind=source_engine, only=insertion_order)
    if set(source_meta.tables) != set(insertion_order):
        raise ValueError("Reflected schema contains dependencies outside the selected database.")
    source_inspector = sa.inspect(source_engine)
    target_inspector = sa.inspect(target_engine)
    existing = set(target_inspector.get_table_names())
    for table_name in insertion_order:
        actual_pk = source_inspector.get_pk_constraint(table_name).get("constrained_columns", [])
        if not actual_pk or actual_pk != schema.tables[table_name].effective_primary_keys:
            raise ValueError(f"Source primary key metadata is missing or stale for '{table_name}'.")
        if target_engine.dialect.name == "mysql":
            # MyISAM and other nontransactional engines cannot preserve old rows on rollback.
            for inspector in [source_inspector] + (
                [target_inspector] if table_name in existing else []
            ):
                engine = inspector.get_table_options(table_name).get("mysql_engine", "")
                if engine.lower() != "innodb":
                    raise ValueError(f"Table '{table_name}' must use InnoDB for atomic refresh.")
        if table_name in existing:
            differences = table_schema_differences(source_inspector, target_inspector, table_name)
            if differences:
                raise ValueError("Target schema is incompatible: " + "; ".join(differences))

    created = created_tables if created_tables is not None else []
    try:
        for table_name in insertion_order:
            if table_name not in existing:
                with target_engine.begin() as conn:
                    source_meta.tables[table_name].create(conn)
                created.append(table_name)
        # Reflection must reproduce constraints faithfully, including dialect-specific
        # cases where the driver returns incomplete metadata without raising.
        fresh_target_inspector = sa.inspect(target_engine)
        for table_name in insertion_order:
            differences = table_schema_differences(
                source_inspector, fresh_target_inspector, table_name
            )
            if differences:
                raise ValueError(
                    "Could not reproduce exact source schema: " + "; ".join(differences)
                )
    except Exception:
        _cleanup_created_tables(target_engine, created)
        created.clear()
        raise
    return source_meta
