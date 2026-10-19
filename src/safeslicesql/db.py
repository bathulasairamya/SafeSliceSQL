"""
safeslicesql.db — Database connection management.

Provides SQLAlchemy engine creation, connection testing, and utility
functions for interacting with MySQL source and target databases.

The source engine should be used in a read-only fashion. The target
engine requires CREATE/DROP/INSERT privileges.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text

from safeslicesql.config import AppConfig, DatabaseConfig

logger = logging.getLogger("safeslicesql.db")


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
        config.user, config.host, config.port, config.database,
    )
    return engine


def get_source_engine(app_config: AppConfig, **kwargs: Any) -> sa.Engine:
    """
    Get an engine configured for the source database.

    This engine should be used for read-only operations: schema
    inspection, workload evaluation, and source-immutability checks.
    """
    logger.info("Creating source engine for database '%s'",
                app_config.source_db.database)
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

    if (
        src.host == tgt.host
        and src.port == tgt.port
        and src.database == tgt.database
    ):
        raise ValueError(
            f"Refusing to create target engine: target database "
            f"'{tgt.database}' is the same as the source database on "
            f"{tgt.host}:{tgt.port}. This would overwrite the source."
        )

    logger.info("Creating target engine for database '%s'",
                tgt.database)
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
        logger.error(
            "Connection test FAILED for %s: %s",
            engine.url.database, exc,
        )
        return False


def get_table_names(engine: sa.Engine) -> list[str]:
    """Return a sorted list of table names in the connected database."""
    inspector = sa.inspect(engine)
    tables = sorted(inspector.get_table_names())
    logger.debug("Found %d tables: %s", len(tables), tables)
    return tables


def get_row_count(engine: sa.Engine, table_name: str) -> int:
    """Return the approximate row count for a table."""
    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT COUNT(*) FROM `{table_name}`")
        )
        count = result.scalar() or 0
    return count


def database_exists(engine: sa.Engine, db_name: str) -> bool:
    """Check whether a database exists on the connected server."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA "
                "WHERE SCHEMA_NAME = :db"
            ),
            {"db": db_name},
        )
        return result.fetchone() is not None
