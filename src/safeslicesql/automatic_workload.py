"""Build structural demonstration queries from a discovered schema."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from safeslicesql.db import quote_ident
from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.types import SchemaMetadata


def generate_workload(schema: SchemaMetadata) -> str:
    """Use real table/column/FK names, without assuming an application domain.

    These queries demonstrate schema coverage. They do not infer an application's
    business queries or user priorities from the dataset alone.
    """
    if not schema.tables:
        raise ValueError("The source contains no tables.")
    if schema.unsupported_features:
        raise ValueError("Unsupported source schema: " + "; ".join(schema.unsupported_features))
    if build_dependency_graph(schema).has_cycles:
        raise ValueError("Cross-table foreign-key cycles are not supported.")
    statements = []
    for name in schema.table_names:
        table = schema.tables[name]
        if not table.effective_primary_keys:
            raise ValueError(f"Table '{name}' needs a declared primary key.")
        quoted = quote_ident(name)
        statements.append(f"SELECT * FROM {quoted}")
        statements.append(f"SELECT COUNT(*) AS row_count FROM {quoted}")
        # Group by a real FK or scalar category when available, rather than
        # assuming a domain-specific column such as department or continent.
        groups = [fk.column for fk in table.foreign_keys]
        if not groups:
            groups = [
                column.name
                for column in table.columns
                if not column.is_primary_key
                and any(kind in column.data_type.upper() for kind in ("CHAR", "TEXT", "ENUM"))
            ]
        if groups:
            group = quote_ident(groups[0])
            statements.append(
                f"SELECT {group} AS group_value,COUNT(*) AS group_count FROM {quoted} GROUP BY {group}"
            )
        for fk in table.foreign_keys:
            if fk.referenced_table not in schema.tables:
                raise ValueError(f"Foreign key from '{name}' refers outside the discovered schema.")
            child, parent = quote_ident(fk.column), quote_ident(fk.referenced_column)
            parent_table = quote_ident(fk.referenced_table)
            statements.append(
                f"SELECT c.* FROM {quoted} c JOIN {parent_table} p ON c.{child}=p.{parent}"
            )
            statements.append(f"SELECT * FROM {quoted} WHERE {child} IS NOT NULL")
    return "-- Automatically generated structural demo workload.\n" + ";\n".join(statements) + ";\n"


def save_automatic_workload(schema: SchemaMetadata, directory: Path) -> Path:
    sql = generate_workload(schema)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"automatic_{uuid4().hex[:12]}.sql"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(sql)
    return path
