"""
safeslicesql.db — Database connection management.

Provides SQLAlchemy engine creation, connection testing, and utility
functions for interacting with MySQL source and target databases.

The source engine should be used in a read-only fashion. The target
engine requires CREATE/DELETE/INSERT and cleanup DROP privileges.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text

from safeslicesql.config import AppConfig, DatabaseConfig

logger = logging.getLogger("safeslicesql.db")


def assert_distinct_databases(source: sa.Engine, target: sa.Engine, target_database: str) -> None:
    """Verify connected database identities before allowing any target writes."""
    same_database = "Refusing to materialize: target is identical to the source database."
    if source is target or source.pool is target.pool:
        raise ValueError(same_database)
    dialect = source.dialect.name
    if dialect != target.dialect.name or dialect not in {"mysql", "sqlite"}:
        raise ValueError("Safe materialization requires matching MySQL or SQLite databases.")
    with source.connect() as src, target.connect() as tgt:
        if dialect == "mysql":
            identity_sql = text("SELECT @@server_uuid, DATABASE(), @@lower_case_table_names")
            src_server, src_db, src_case = src.execute(identity_sql).one()
            tgt_server, tgt_db, tgt_case = tgt.execute(identity_sql).one()
            if not src_server or not tgt_server or not src_db or not tgt_db:
                raise ValueError("Could not establish source and target database identities.")

            def normalize(name: str, insensitive: int) -> str:
                return name.lower() if insensitive else name

            if src_server == tgt_server and normalize(src_db, src_case) == normalize(
                tgt_db, tgt_case
            ):
                raise ValueError(same_database)
            if normalize(target_database, tgt_case) != normalize(tgt_db, tgt_case):
                raise ValueError(
                    "Target database name does not match the connected target database."
                )
        else:
            # SQLite's connected filename resolves relative paths and URI aliases.
            src_path = next(
                r[2] for r in src.exec_driver_sql("PRAGMA database_list") if r[1] == "main"
            )
            tgt_path = next(
                r[2] for r in tgt.exec_driver_sql("PRAGMA database_list") if r[1] == "main"
            )
            if src.connection.driver_connection is tgt.connection.driver_connection:
                raise ValueError(same_database)
            if src_path and tgt_path:
                if Path(src_path).resolve() == Path(tgt_path).resolve() or os.path.samefile(
                    src_path, tgt_path
                ):
                    raise ValueError(same_database)
            elif (not src_path and source.url.database not in (None, "", ":memory:")) or (
                not tgt_path and target.url.database not in (None, "", ":memory:")
            ):
                # Named shared-memory URI databases cannot be identified by filename.
                raise ValueError(
                    "Named shared-memory SQLite databases are not supported for safe materialization."
                )


def create_engine(
    config: DatabaseConfig,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_recycle: int = 3600,
    echo: bool = False,
) -> sa.Engine:
    """
    Create a SQLAlchemy engine from a DatabaseConfig.

    Parameters
    ----------
    config : DatabaseConfig
        Database connection parameters.
    pool_size : int
        Number of persistent connections in the pool.
    max_overflow : int
        Max temporary connections beyond pool_size.
    pool_recycle : int
        Seconds before a connection is recycled (prevents MySQL timeout).
    echo : bool
        If True, log all SQL statements (DEBUG only).

    Returns
    -------
    sqlalchemy.Engine
    """
    engine = sa.create_engine(
        config.url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
        echo=echo,
    )
    logger.info(
        "Created engine for %s@%s:%d/%s",
        config.user,
        config.host,
        config.port,
        config.database,
    )
    return engine


def get_source_engine(app_config: AppConfig, **kwargs: Any) -> sa.Engine:
    """
    Get an engine configured for the source database.

    This engine should be used for read-only operations: schema
    inspection, workload evaluation, and source-immutability checks.
    """
    logger.info("Creating source engine for database '%s'", app_config.source_db.database)
    return create_engine(app_config.source_db, **kwargs)


def get_target_engine(app_config: AppConfig, **kwargs: Any) -> sa.Engine:
    """
    Get an engine configured for the target database.

    This engine is used for creating schemas, inserting subset rows,
    and running target-side workload evaluations.

    Raises
    ------
    ValueError
        If target database name matches the source database name on the
        same host (safety guard against accidental overwrites).
    """
    src = app_config.source_db
    tgt = app_config.target_db

    if src.host == tgt.host and src.port == tgt.port and src.database == tgt.database:
        raise ValueError(
            f"Refusing to create target engine: target database "
            f"'{tgt.database}' is the same as the source database on "
            f"{tgt.host}:{tgt.port}. This would overwrite the source."
        )

    logger.info("Creating target engine for database '%s'", tgt.database)
    return create_engine(tgt, **kwargs)


def test_connection(engine: sa.Engine) -> bool:
    """
    Verify that the engine can connect to MySQL and execute a query.

    Returns
    -------
    bool
        True if the connection succeeds, False otherwise.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            result.fetchone()
        logger.info("Connection test passed for %s", engine.url.database)
        return True
    except Exception as exc:
        original_error = getattr(exc, "orig", None)
        error_code = None
        if original_error is not None:
            args = getattr(original_error, "args", ())
            if args and isinstance(args[0], int):
                error_code = args[0]
        if error_code is not None:
            logger.error(
                "Connection test FAILED for %s (database error code %s)",
                engine.url.database,
                error_code,
            )
        else:
            logger.error(
                "Connection test FAILED for %s (%s)",
                engine.url.database,
                type(exc).__name__,
            )
        return False


def get_table_names(engine: sa.Engine) -> list[str]:
    """Return a sorted list of table names in the connected database."""
    inspector = sa.inspect(engine)
    tables = sorted(inspector.get_table_names())
    logger.debug("Found %d tables: %s", len(tables), tables)
    return tables


def quote_ident(name: str) -> str:
    """Safely quote a SQL identifier using backticks, escaping inner backticks."""
    return f"`{str(name).replace('`', '``')}`"


_quote_ident = quote_ident


def get_row_count(engine: sa.Engine, table_name: str) -> int:
    """Return the exact row count for a table."""
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {_quote_ident(table_name)}"))
        count = result.scalar() or 0
    return count


def database_exists(engine: sa.Engine, db_name: str) -> bool:
    """Check whether a database exists on the connected server."""
    if engine.dialect.name == "sqlite":
        return True
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :db"),
                {"db": db_name},
            )
            return result.fetchone() is not None
    except Exception as exc:
        logger.debug("database_exists check failed on %s: %s", db_name, exc)
        return False
