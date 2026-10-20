"""
safeslicesql.schema_analyzer — Source database schema introspection.

Discovers tables, columns, primary keys, foreign keys, nullability,
and row counts from a live MySQL (or SQLAlchemy-supported) database.
Detects and reports unsupported schema features (composite keys,
composite foreign keys, cycles, etc.) per the v1 scope definition.

Owner: M1
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine

from safeslicesql.types import (
    ColumnMetadata,
    ForeignKeyMetadata,
    SchemaMetadata,
    TableMetadata,
)

logger = logging.getLogger("safeslicesql.schema_analyzer")


def _quote_ident(name: str) -> str:
    """Safely quote a SQL identifier using backticks."""
    return f"`{name.replace('`', '``')}`"


def inspect_schema(engine: Engine) -> SchemaMetadata:
    """
    Introspect the source database and return structured metadata.

    Discovers:
    - Tables, columns, data types, nullability
    - Single-column and composite primary keys
    - Single-column foreign keys with parent/child direction
    - Approximate or exact row counts per table

    Detects and reports as unsupported:
    - Composite primary keys (flagged in unsupported_features)
    - Composite foreign keys (flagged in unsupported_features)
    - Missing primary keys (flagged in unsupported_features)
    - Cyclic foreign-key dependencies (flagged in unsupported_features)

    Parameters
    ----------
    engine : sqlalchemy.Engine
        A connected engine pointing to the source database.

    Returns
    -------
    SchemaMetadata
        Complete metadata including unsupported-feature warnings.
    """
    inspector = sa.inspect(engine)
    db_name = engine.url.database or "default"
    logger.info("Starting schema introspection for database '%s'", db_name)

    schema = SchemaMetadata(database_name=db_name)
    table_names = sorted(inspector.get_table_names())
    logger.debug("Discovered %d tables in database '%s': %s", len(table_names), db_name, table_names)

    # Pre-fetch row counts using a single connection (fast INFORMATION_SCHEMA on MySQL)
    table_row_counts: dict[str, int] = {}
    try:
        with engine.connect() as conn:
            if engine.dialect.name == "mysql":
                try:
                    stmt = sa.text(
                        "SELECT TABLE_NAME, TABLE_ROWS FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_SCHEMA = :db"
                    )
                    rows_result = conn.execute(stmt, {"db": db_name})
                    for r_table, r_count in rows_result:
                        if r_count is not None:
                            table_row_counts[r_table] = int(r_count)
                except Exception as exc:
                    logger.debug("Could not query INFORMATION_SCHEMA.TABLES: %s", exc)

            for table_name in table_names:
                if table_name in table_row_counts:
                    continue
                try:
                    res = conn.execute(sa.text(f"SELECT COUNT(*) FROM {_quote_ident(table_name)}"))
                    table_row_counts[table_name] = res.scalar() or 0
                except Exception as exc:
                    logger.debug("Could not query row count for table '%s': %s", table_name, exc)
                    table_row_counts[table_name] = 0
    except Exception as exc:
        logger.warning("Could not establish connection to retrieve row counts: %s", exc)

    for table_name in table_names:
        logger.debug("Introspecting table: %s", table_name)

        # 1. Columns
        columns: list[ColumnMetadata] = []
        try:
            columns_raw = inspector.get_columns(table_name)
            for col in columns_raw:
                default_val = col.get("default")
                col_meta = ColumnMetadata(
                    name=col["name"],
                    data_type=str(col["type"]),
                    nullable=bool(col.get("nullable", True)),
                    column_default=str(default_val) if default_val is not None else None,
                    extra=str(col.get("autoincrement", "")) if col.get("autoincrement") else "",
                )
                columns.append(col_meta)
        except Exception as exc:
            logger.warning("Could not read columns for table '%s': %s", table_name, exc)

        # 2. Primary Key
        primary_key: str | None = None
        primary_keys: list[str] = []
        try:
            pk_constraint = inspector.get_pk_constraint(table_name)
            constrained_cols = pk_constraint.get("constrained_columns", [])
            primary_keys = list(constrained_cols)
            if len(constrained_cols) == 1:
                primary_key = constrained_cols[0]
                for c in columns:
                    if c.name == primary_key:
                        c.is_primary_key = True
            elif len(constrained_cols) > 1:
                for c in columns:
                    if c.name in constrained_cols:
                        c.is_primary_key = True
                warn_msg = (
                    f"Table '{table_name}' has composite primary key: "
                    f"{constrained_cols}."
                )
                logger.info(warn_msg)
                schema.unsupported_features.append(warn_msg)
            else:
                warn_msg = f"Table '{table_name}' has no primary key defined."
                logger.warning(warn_msg)
                schema.unsupported_features.append(warn_msg)
        except Exception as exc:
            logger.warning("Could not inspect primary key for table '%s': %s", table_name, exc)

        # 3. Foreign Keys
        foreign_keys: list[ForeignKeyMetadata] = []
        try:
            fks_raw = inspector.get_foreign_keys(table_name)
            for fk in fks_raw:
                c_cols = fk.get("constrained_columns", [])
                r_table = fk.get("referred_table", "")
                r_cols = fk.get("referred_columns", [])
                constraint_name = fk.get("name") or f"fk_{table_name}_{'_'.join(c_cols)}"

                if len(c_cols) == 1 and len(r_cols) == 1:
                    fk_meta = ForeignKeyMetadata(
                        constraint_name=constraint_name,
                        column=c_cols[0],
                        referenced_table=r_table,
                        referenced_column=r_cols[0],
                    )
                    foreign_keys.append(fk_meta)
                elif len(c_cols) > 1 or len(r_cols) > 1:
                    warn_msg = (
                        f"Table '{table_name}' has composite foreign key '{constraint_name}': "
                        f"({', '.join(c_cols)}) -> {r_table}({', '.join(r_cols)}). "
                        f"v1 supports single-column foreign keys only."
                    )
                    logger.warning(warn_msg)
                    schema.unsupported_features.append(warn_msg)
        except Exception as exc:
            logger.warning("Could not inspect foreign keys for table '%s': %s", table_name, exc)

        row_count = table_row_counts.get(table_name, 0)

        table_meta = TableMetadata(
            name=table_name,
            columns=columns,
            primary_key=primary_key,
            primary_keys=primary_keys,
            row_count=row_count,
            foreign_keys=foreign_keys,
        )
        schema.tables[table_name] = table_meta

    # 5. Check for cyclic dependencies in the schema
    from safeslicesql.dependency_graph import build_dependency_graph
    try:
        graph = build_dependency_graph(schema)
        if graph.has_cycles:
            warn_msg = (
                f"Cyclic foreign-key dependencies detected in database '{db_name}'. "
                f"v1 supports acyclic foreign-key graphs only."
            )
            logger.warning(warn_msg)
            schema.unsupported_features.append(warn_msg)
    except Exception as exc:
        logger.debug("Dependency graph check during schema introspection failed: %s", exc)

    logger.info(
        "Schema introspection complete: %d tables, %d total rows, %d unsupported feature warnings",
        len(schema.tables), schema.total_row_count, len(schema.unsupported_features),
    )
    return schema
