"""Compare reflected schemas without relying on constraint names."""

from __future__ import annotations

from typing import Any


def _column_signature(column: dict[str, Any], inspector: Any) -> tuple:
    return (
        column["name"],
        str(column["type"].compile(dialect=inspector.bind.dialect)),
        bool(column.get("nullable", True)),
        column.get("default"),
        column.get("autoincrement"),
        tuple(sorted((column.get("computed") or {}).items())),
    )


def _foreign_keys(inspector: Any, table: str) -> set[tuple]:
    signatures = set()
    for fk in inspector.get_foreign_keys(table):
        referred_schema = fk.get("referred_schema")
        if referred_schema == inspector.default_schema_name:
            referred_schema = None
        signatures.add(
            (
                tuple(fk.get("constrained_columns", [])),
                referred_schema,
                fk.get("referred_table"),
                tuple(fk.get("referred_columns", [])),
                tuple(sorted((fk.get("options") or {}).items())),
            )
        )
    return signatures


def _unique_keys(inspector: Any, table: str) -> set[tuple]:
    keys = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table)}
    keys.update(tuple(i["column_names"]) for i in inspector.get_indexes(table) if i.get("unique"))
    if inspector.bind.dialect.name == "sqlite":
        # SQLite reflection can miss inline UNIQUE clauses containing defaults.
        # Inspect the authoritative indexes, including sqlite_autoindex entries.
        quote = inspector.bind.dialect.identifier_preparer.quote
        with inspector.bind.connect() as connection:
            for index in connection.exec_driver_sql(
                f"PRAGMA index_list({quote(table)})"
            ).fetchall():
                if index[2] and index[3] != "pk":
                    columns = connection.exec_driver_sql(
                        f"PRAGMA index_info({quote(index[1])})"
                    ).fetchall()
                    keys.add(tuple(column[2] for column in columns))
    return keys


def table_schema_differences(source: Any, target: Any, table: str) -> list[str]:
    """Return structural differences; inspection failures deliberately propagate."""
    differences = []
    if [_column_signature(c, source) for c in source.get_columns(table)] != [
        _column_signature(c, target) for c in target.get_columns(table)
    ]:
        differences.append(f"Table '{table}' has different column definitions.")
    if source.get_pk_constraint(table).get("constrained_columns", []) != target.get_pk_constraint(
        table
    ).get("constrained_columns", []):
        differences.append(f"Table '{table}' has different primary keys.")
    if _foreign_keys(source, table) != _foreign_keys(target, table):
        differences.append(f"Table '{table}' has different foreign keys.")
    if _unique_keys(source, table) != _unique_keys(target, table):
        differences.append(f"Table '{table}' has different unique constraints.")
    source_checks = {c["sqltext"] for c in source.get_check_constraints(table)}
    target_checks = {c["sqltext"] for c in target.get_check_constraints(table)}
    if source_checks != target_checks:
        differences.append(f"Table '{table}' has different check constraints.")
    return differences
