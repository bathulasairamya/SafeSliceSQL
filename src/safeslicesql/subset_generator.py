"""
safeslicesql.subset_generator — Target database creation and population.

Creates a fresh target schema, reproduces supported table definitions,
and inserts selected rows in safe dependency order (parents before
children) while preserving original primary-key values.

Uses transactions for safety and atomicity. Does NOT disable foreign-key checks.
Enforces hard row budget limits and prevents source database overwrites.

Owner: M1
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine

from safeslicesql.db import quote_ident
from safeslicesql.dependency_graph import DependencyGraph
from safeslicesql.types import (
    GenerationResult,
    SchemaMetadata,
    SelectionResult,
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
    3. Ensure target database exists on the server.
    4. Determine safe insertion order via topological sort (parents first).
    5. Reproduce table definitions on target and refresh existing tables.
    6. Insert selected rows table-by-table, parents first, in a transaction.
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

    # 1. Safety check: target must not match source database on same host/port
    src_db = (source_engine.url.database or "").lower()
    tgt_db = target_database.lower()
    if (
        src_db
        and tgt_db
        and src_db == tgt_db
        and source_engine.url.host == target_engine.url.host
        and source_engine.url.port == target_engine.url.port
    ):
        msg = (
            f"Refusing to materialize: target database '{target_database}' is identical "
            f"to the source database on {source_engine.url.host}:{source_engine.url.port}. "
            "This safeguard prevents accidental modification of source data."
        )
        logger.error(msg)
        raise ValueError(msg)

    # 2. Hard row budget check
    total_selected = sum(len(pks) for pks in selection.selected_rows.values())
    if selection.budget > 0 and total_selected > selection.budget:
        err_msg = (
            f"Selection size ({total_selected} rows) exceeds hard budget limit "
            f"of {selection.budget} rows. Materialization aborted."
        )
        logger.error(err_msg)
        return GenerationResult(
            target_database=target_database,
            success=False,
            error_message=err_msg,
        )

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

    # 4. Ensure target database exists (for MySQL)
    _ensure_target_database_exists(target_engine, target_database)

    # 5. Reproduce table definitions on target database
    try:
        target_meta = _reproduce_schema_on_target(
            source_engine=source_engine,
            target_engine=target_engine,
            schema=schema,
            insertion_order=insertion_order,
        )
    except Exception as exc:
        err_msg = f"Failed to reproduce schema on target database: {exc}"
        logger.error(err_msg, exc_info=True)
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
            for tbl_name in insertion_order:
                selected_pks = selection.selected_rows.get(tbl_name, [])
                if not selected_pks:
                    rows_inserted[tbl_name] = 0
                    continue

                tbl_meta = schema.tables.get(tbl_name)
                pk_cols = tbl_meta.effective_primary_keys if tbl_meta else []
                if not pk_cols:
                    # Check if 'id' exists on table
                    col_names = tbl_meta.column_names if tbl_meta else []
                    if "id" in col_names:
                        pk_cols = ["id"]
                    else:
                        logger.warning(
                            "Skipping table '%s': no primary key defined for row selection lookup",
                            tbl_name,
                        )
                        rows_inserted[tbl_name] = 0
                        continue

                target_table = target_meta.tables.get(tbl_name)
                if target_table is None:
                    raise RuntimeError(f"Table '{tbl_name}' not found in target metadata")

                inserted_for_table = 0
                pks_list = list(selected_pks)
                quoted_table = f"`{tbl_name.replace('`', '``')}`"

                for i in range(0, len(pks_list), chunk_size):
                    chunk = pks_list[i : i + chunk_size]

                    if len(pk_cols) == 1:
                        pk_col = pk_cols[0]
                        quoted_pk = f"`{pk_col.replace('`', '``')}`"
                        stmt = sa.text(
                            f"SELECT * FROM {quoted_table} WHERE {quoted_pk} IN :pks"
                        ).bindparams(sa.bindparam("pks", expanding=True))
                        res = s_conn.execute(stmt, {"pks": chunk})
                    else:
                        # Composite key condition: (col1 = :p0_0 AND col2 = :p0_1) OR ...
                        conds = []
                        params = {}
                        for idx, tup in enumerate(chunk):
                            tup_vals = tup if isinstance(tup, (tuple, list)) else (tup,)
                            sub_conds = []
                            for c_idx, c_name in enumerate(pk_cols):
                                p_name = f"p_{idx}_{c_idx}"
                                q_col = f"`{c_name.replace('`', '``')}`"
                                sub_conds.append(f"{q_col} = :{p_name}")
                                params[p_name] = tup_vals[c_idx] if c_idx < len(tup_vals) else None
                            conds.append(f"({' AND '.join(sub_conds)})")

                        where_clause = " OR ".join(conds)
                        stmt = sa.text(f"SELECT * FROM {quoted_table} WHERE {where_clause}")
                        res = s_conn.execute(stmt, params)

                    row_dicts = [dict(r) for r in res.mappings().all()]

                    if row_dicts:
                        t_conn.execute(sa.insert(target_table), row_dicts)
                        inserted_for_table += len(row_dicts)

                rows_inserted[tbl_name] = inserted_for_table
                logger.debug(
                    "Inserted %d rows into target table '%s'",
                    inserted_for_table, tbl_name,
                )

        total_inserted = sum(rows_inserted.values())
        logger.info(
            "Materialization succeeded: %d total rows inserted across %d tables",
            total_inserted, len(insertion_order),
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
        return GenerationResult(
            target_database=target_database,
            tables_created=list(target_meta.tables.keys()) if 'target_meta' in locals() else [],
            rows_inserted=rows_inserted,
            total_rows_inserted=sum(rows_inserted.values()),
            insertion_order=insertion_order,
            success=False,
            error_message=err_msg,
        )


def _ensure_target_database_exists(target_engine: Engine, target_database: str) -> None:
    """Ensure that the target database schema exists on MySQL servers."""
    if target_engine.dialect.name == "mysql":
        try:
            # Connect to server default database to execute CREATE DATABASE
            server_url = target_engine.url.set(database="mysql")
            server_engine = sa.create_engine(server_url)
            with server_engine.connect() as conn:
                conn.execute(sa.text(f"CREATE DATABASE IF NOT EXISTS {quote_ident(target_database)}"))
                conn.commit()
            server_engine.dispose()
        except Exception as exc:
            logger.debug(
                "Could not execute CREATE DATABASE on target server: %s; proceeding assuming target DB exists",
                exc,
            )


def _reproduce_schema_on_target(
    source_engine: Engine,
    target_engine: Engine,
    schema: SchemaMetadata,
    insertion_order: list[str],
) -> sa.MetaData:
    """
    Reproduce table definitions from source to target.

    Reflects the exact schema and constraints from the source database,
    drops existing target tables in reverse topological order (children first),
    and creates new target tables in topological order (parents first).
    """
    # Try reflection from source engine first
    try:
        source_meta = sa.MetaData()
        source_meta.reflect(bind=source_engine, only=insertion_order)

        # Drop existing target tables in reverse topological order
        with target_engine.begin() as conn:
            for tbl_name in reversed(insertion_order):
                if tbl_name in source_meta.tables:
                    source_meta.tables[tbl_name].drop(bind=conn, checkfirst=True)

        # Create target tables in parent-first topological order
        with target_engine.begin() as conn:
            for tbl_name in insertion_order:
                if tbl_name in source_meta.tables:
                    source_meta.tables[tbl_name].create(bind=conn, checkfirst=True)

        return source_meta

    except Exception as exc:
        logger.debug(
            "Source reflection for schema reproduction failed (%s); building from SchemaMetadata",
            exc,
        )

        fallback_meta = sa.MetaData()
        # Drop existing tables if present
        try:
            inspector = sa.inspect(target_engine)
            existing = set(inspector.get_table_names())
            with target_engine.begin() as conn:
                for tbl_name in reversed(insertion_order):
                    if tbl_name in existing:
                        conn.execute(sa.text(f"DROP TABLE IF EXISTS `{tbl_name}`"))
        except Exception as drop_exc:
            logger.debug("Drop table fallback encountered: %s", drop_exc)

        # Build Table objects from TableMetadata
        for tbl_name in insertion_order:
            tbl_meta = schema.tables[tbl_name]
            cols = []
            for c in tbl_meta.columns:
                col_type: Any = sa.Integer
                dt = c.data_type.upper()
                if any(t in dt for t in ("CHAR", "TEXT", "VARCHAR", "STRING")):
                    col_type = sa.String(255)
                elif any(t in dt for t in ("FLOAT", "DOUBLE", "DECIMAL", "NUMERIC")):
                    col_type = sa.Float
                elif "DATE" in dt or "TIME" in dt:
                    col_type = sa.Date

                cols.append(
                    sa.Column(
                        c.name,
                        col_type,
                        primary_key=c.is_primary_key,
                        nullable=c.nullable,
                    )
                )

            fks = []
            for fk in tbl_meta.foreign_keys:
                fks.append(
                    sa.ForeignKeyConstraint(
                        [fk.column],
                        [f"{fk.referenced_table}.{fk.referenced_column}"],
                        name=fk.constraint_name,
                    )
                )

            sa.Table(tbl_name, fallback_meta, *cols, *fks)

        fallback_meta.create_all(bind=target_engine)
        return fallback_meta
