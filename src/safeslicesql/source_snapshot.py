"""Streaming source content fingerprints for before/after verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import sqlalchemy as sa

from safeslicesql.db import quote_ident


@dataclass(frozen=True)
class SourceSnapshot:
    fingerprint: str
    row_count: int


def capture_source_snapshot(engine: sa.Engine) -> SourceSnapshot:
    """Fingerprint schema and every row, including duplicate rows and value types.

    Order-independent row hashes avoid relying on query scan order or buffering
    the database. This verifies endpoint contents, not intervening write history.
    """
    tables = []
    total_rows = 0
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        for table in sorted(inspector.get_table_names()):
            schema = {
                "columns": inspector.get_columns(table),
                "pk": inspector.get_pk_constraint(table),
                "fks": inspector.get_foreign_keys(table),
                "unique": inspector.get_unique_constraints(table),
                "indexes": inspector.get_indexes(table),
                "checks": inspector.get_check_constraints(table),
            }
            if engine.dialect.name == "sqlite":
                schema["ddl"] = connection.execute(
                    sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
                    {"name": table},
                ).scalar_one()
            elif engine.dialect.name == "mysql":
                schema["ddl"] = connection.exec_driver_sql(
                    f"SHOW CREATE TABLE {quote_ident(table)}"
                ).one()[1]
            checksum = 0
            count = 0
            result = connection.execution_options(stream_results=True).exec_driver_sql(
                f"SELECT * FROM {quote_ident(table)}"
            )
            try:
                for row in result:
                    encoded = json.dumps(
                        [(type(value).__name__, str(value)) for value in row],
                        ensure_ascii=True,
                    ).encode()
                    checksum = (
                        checksum + int.from_bytes(hashlib.sha256(encoded).digest(), "big")
                    ) % (1 << 256)
                    count += 1
            finally:
                result.close()
            total_rows += count
            tables.append((table, schema, count, checksum))
    payload = json.dumps(tables, sort_keys=True, default=str).encode()
    return SourceSnapshot(hashlib.sha256(payload).hexdigest(), total_rows)
