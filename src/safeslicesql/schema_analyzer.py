"""
safeslicesql.schema_analyzer — Source database schema introspection.

Discovers tables, columns, primary keys, foreign keys, nullability,
and row counts from a live MySQL database. Detects and reports
unsupported schema features (composite keys, cycles, etc.).

Owner: M1
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine

from safeslicesql.types import SchemaMetadata

logger = logging.getLogger("safeslicesql.schema_analyzer")


def inspect_schema(engine: Engine) -> SchemaMetadata:
    """
    Introspect the source database and return structured metadata.

    Discovers:
    - Tables, columns, data types, nullability
    - Single-column primary keys
    - Single-column foreign keys with parent/child direction
    - Approximate row counts per table

    Detects and reports as unsupported:
    - Composite primary keys
    - Composite foreign keys
    - Any schema features outside the documented scope

    Parameters
    ----------
    engine : sqlalchemy.Engine
        A connected engine pointing to the source database.

    Returns
    -------
    SchemaMetadata
        Complete metadata including unsupported-feature warnings.
    """
    raise NotImplementedError("Phase 2: implement schema introspection")
