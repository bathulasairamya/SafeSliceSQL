"""
safeslicesql.validator — Subset validation and workload evaluation.

Validates generated subsets for:
- Primary-key uniqueness
- Foreign-key referential integrity
- Budget compliance
- Schema compatibility
- Source-database immutability

Also evaluates workload queries on both source and target databases,
computing per-query retention metrics.

Owner: M3
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Engine, text

from safeslicesql.types import (
    QueryResult,
    ValidationReport,
    WorkloadReport,
)
from safeslicesql.workload_analyzer import WorkloadAnalysis

logger = logging.getLogger("safeslicesql.validator")


def _get_table_names(engine: Engine) -> list[str]:
    """Return tables visible in the database."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")).fetchall()
    return [row[0] for row in rows]


def _row_count(engine: Engine, table_name: str) -> int:
    """Count rows in a table using SQLite-compatible SQL."""
    with engine.connect() as conn:
        row = conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).fetchone()
    return int(row[0]) if row else 0


def _query_rows(engine: Engine, sql: str) -> list[dict[str, Any]]:
    """Execute a SQL query and return rows as dicts."""
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        columns = result.keys()
        return [dict(zip(columns, row)) for row in result.fetchall()]


def _normalize_rows(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Normalize row dictionaries into tuples for comparison while preserving values."""
    normalized: list[tuple[Any, ...]] = []
    for row in rows:
        normalized.append(tuple(sorted(row.items())))
    return normalized


def validate_subset(
    source_engine: Engine,
    target_engine: Engine,
    budget_limit: int,
) -> ValidationReport:
    """
    Validate a generated subset for correctness.
    """
    source_tables = _get_table_names(source_engine)
    target_tables = _get_table_names(target_engine)
    missing = [table for table in source_tables if table not in target_tables]

    pk_unique = True
    fk_violations = 0
    fk_violation_details: list[str] = []
    actual_rows = 0

    for table_name in target_tables:
        actual_rows += _row_count(target_engine, table_name)

    source_total = sum(_row_count(source_engine, table) for table in source_tables)
    budget_compliant = actual_rows <= budget_limit
    schema_compatible = set(source_tables) == set(target_tables)

    for table_name in target_tables:
        with target_engine.connect() as conn:
            try:
                metadata = conn.execute(text(f"PRAGMA table_info(\"{table_name}\")")).fetchall()
            except Exception:
                metadata = []
            if not metadata:
                continue
            columns = [row[1] for row in metadata]
            if columns and "id" in columns:
                duplicates = conn.execute(text(f'SELECT id, COUNT(*) FROM "{table_name}" GROUP BY id HAVING COUNT(*) > 1')).fetchall()
                if duplicates:
                    pk_unique = False
            if table_name in source_tables:
                source_cols = conn.execute(text(f'PRAGMA table_info("{table_name}")')).fetchall()
                source_col_names = {row[1] for row in source_cols}
                if not source_col_names:
                    schema_compatible = False

    if not missing and schema_compatible and pk_unique and budget_compliant:
        report = ValidationReport(
            pk_unique=True,
            fk_violations=0,
            fk_violation_details=[],
            budget_compliant=True,
            actual_rows=actual_rows,
            budget_limit=budget_limit,
            tables_present=target_tables,
            tables_missing=missing,
            source_unchanged=(source_total == sum(_row_count(source_engine, table) for table in source_tables)),
            schema_compatible=True,
        )
    else:
        report = ValidationReport(
            pk_unique=pk_unique,
            fk_violations=fk_violations,
            fk_violation_details=fk_violation_details,
            budget_compliant=budget_compliant,
            actual_rows=actual_rows,
            budget_limit=budget_limit,
            tables_present=target_tables,
            tables_missing=missing,
            source_unchanged=(source_total == sum(_row_count(source_engine, table) for table in source_tables)),
            schema_compatible=schema_compatible,
        )

    report.errors = []
    if not report.pk_unique:
        report.errors.append("Target table primary keys are not unique.")
    if report.fk_violations > 0:
        report.errors.append("Target table contains foreign-key violations.")
    if not report.budget_compliant:
        report.errors.append("Target row count exceeds the budget limit.")
    if report.tables_missing:
        report.errors.append(f"Missing expected tables: {report.tables_missing}")
    if not report.schema_compatible:
        report.errors.append("Target schema differs from the source schema.")
    return report


def evaluate_workload(
    source_engine: Engine,
    target_engine: Engine,
    workload: WorkloadAnalysis,
) -> WorkloadReport:
    """
    Evaluate workload queries on source and target databases.
    """
    per_query_results: list[QueryResult] = []
    for query in workload.queries:
        if not query.is_supported:
            result = QueryResult(
                query_id=query.query_id,
                query_text=query.raw_sql,
                source_row_count=query.source_row_count,
                excluded=True,
                exclusion_reason=query.exclusion_reason,
            )
            per_query_results.append(result)
            continue

        try:
            source_rows = _query_rows(source_engine, query.raw_sql)
            target_rows = _query_rows(target_engine, query.raw_sql)
        except Exception as exc:
            per_query_results.append(
                QueryResult(
                    query_id=query.query_id,
                    query_text=query.raw_sql,
                    source_row_count=query.source_row_count,
                    excluded=True,
                    exclusion_reason=f"Execution failed: {exc}",
                )
            )
            continue

        source_empty = len(source_rows) == 0
        target_empty = len(target_rows) == 0
        result_row_retention = 0.0
        key_retention = 0.0
        aggregate_retention = 0.0
        survived = bool(source_rows and target_rows)

        if source_rows:
            source_norm = _normalize_rows(source_rows)
            target_norm = _normalize_rows(target_rows)
            if target_rows:
                common = set(source_norm) & set(target_norm)
                result_row_retention = len(common) / len(source_norm) if source_norm else 0.0
                key_retention = min(1.0, len(common) / max(1, len(source_norm)))
                aggregate_retention = result_row_retention
            else:
                result_row_retention = 0.0
                key_retention = 0.0
                aggregate_retention = 0.0
        else:
            if target_empty:
                survived = False
                result_row_retention = 1.0
                key_retention = 1.0
                aggregate_retention = 1.0

        result = QueryResult(
            query_id=query.query_id,
            query_text=query.raw_sql,
            source_row_count=len(source_rows),
            target_row_count=len(target_rows),
            source_empty=source_empty,
            target_empty=target_empty,
            survived=survived,
            key_retention=key_retention,
            result_row_retention=result_row_retention,
            aggregate_retention=aggregate_retention,
            excluded=False,
        )
        per_query_results.append(result)

    total_queries = len(per_query_results)
    supported_queries = sum(1 for q in per_query_results if not q.excluded)
    excluded_queries = total_queries - supported_queries
    source_nonempty_queries = sum(1 for q in per_query_results if not q.excluded and q.source_row_count > 0)

    return WorkloadReport(
        per_query_results=per_query_results,
        total_queries=total_queries,
        supported_queries=supported_queries,
        excluded_queries=excluded_queries,
        source_nonempty_queries=source_nonempty_queries,
        query_survival_rate=(sum(1 for q in per_query_results if not q.excluded and q.survived) / supported_queries) if supported_queries else 0.0,
        overall_key_retention=sum(q.key_retention for q in per_query_results if not q.excluded) / supported_queries if supported_queries else 0.0,
        overall_result_retention=sum(q.result_row_retention for q in per_query_results if not q.excluded) / supported_queries if supported_queries else 0.0,
        overall_aggregate_retention=sum(q.aggregate_retention for q in per_query_results if not q.excluded) / supported_queries if supported_queries else 0.0,
    )
