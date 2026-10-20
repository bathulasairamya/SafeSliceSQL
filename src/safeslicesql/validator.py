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
import time
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine, text

from safeslicesql.db import quote_ident
from safeslicesql.types import (
    QueryResult,
    ValidationReport,
    WorkloadReport,
)
from safeslicesql.workload_analyzer import WorkloadAnalysis

logger = logging.getLogger("safeslicesql.validator")

_quote_ident = quote_ident


def _get_table_names(engine: Engine) -> list[str]:
    """Return tables visible in the database across SQLite and MySQL."""
    inspector = sa.inspect(engine)
    return sorted(inspector.get_table_names())


def _row_count(engine: Engine, table_name: str) -> int:
    """Count rows in a table safely across dialects."""
    with engine.connect() as conn:
        stmt = sa.text(f"SELECT COUNT(*) FROM {_quote_ident(table_name)}")
        res = conn.execute(stmt)
        return int(res.scalar() or 0)


def _timed_query_rows(engine: Engine, sql: str) -> tuple[list[dict[str, Any]], float]:
    """Execute a SQL query and return rows along with execution time in seconds."""
    t0 = time.perf_counter()
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
    elapsed = time.perf_counter() - t0
    return rows, elapsed


def _query_rows(engine: Engine, sql: str) -> list[dict[str, Any]]:
    """Execute a SQL query and return rows as dicts."""
    rows, _ = _timed_query_rows(engine, sql)
    return rows


def _normalize_rows(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Normalize row dictionaries into tuples for comparison while preserving values."""
    normalized: list[tuple[Any, ...]] = []
    for row in rows:
        normalized.append(tuple(sorted((k, str(v) if v is not None else None) for k, v in row.items())))
    return normalized


def validate_subset(
    source_engine: Engine,
    target_engine: Engine,
    budget_limit: int,
    initial_source_row_count: int | None = None,
) -> ValidationReport:
    """
    Validate a generated subset for correctness across any SQLAlchemy engine.

    Checks:
    - Target tables presence and column schema compatibility
    - Primary-key uniqueness across all primary key columns
    - Foreign-key referential integrity (0 orphaned references)
    - Hard row budget compliance
    - Source database unchanged (compared against initial_source_row_count if provided)
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

    target_inspector = sa.inspect(target_engine)
    source_inspector = sa.inspect(source_engine)

    # 1. Primary-key uniqueness on target tables
    for table_name in target_tables:
        pk_info = target_inspector.get_pk_constraint(table_name)
        pk_cols = pk_info.get("constrained_columns", [])
        if pk_cols:
            quoted_cols = ", ".join(_quote_ident(c) for c in pk_cols)
            check_pk_sql = (
                f"SELECT {quoted_cols}, COUNT(*) FROM {_quote_ident(table_name)} "
                f"GROUP BY {quoted_cols} HAVING COUNT(*) > 1"
            )
            with target_engine.connect() as conn:
                try:
                    dups = conn.execute(sa.text(check_pk_sql)).fetchall()
                    if dups:
                        pk_unique = False
                except Exception as exc:
                    logger.debug("PK check on %s failed: %s", table_name, exc)

        # 2. Check column schema compatibility
        if table_name in source_tables:
            src_cols = {c["name"] for c in source_inspector.get_columns(table_name)}
            tgt_cols = {c["name"] for c in target_inspector.get_columns(table_name)}
            if src_cols != tgt_cols:
                schema_compatible = False

    # 3. Real foreign-key integrity check: look for orphaned child records
    for child_table in target_tables:
        fks = target_inspector.get_foreign_keys(child_table)
        for fk in fks:
            parent_table = fk.get("referred_table")
            c_cols = fk.get("constrained_columns", [])
            p_cols = fk.get("referred_columns", [])

            if not parent_table or not c_cols or not p_cols:
                continue

            if parent_table not in target_tables:
                fk_violations += 1
                fk_violation_details.append(
                    f"FK constraint references missing parent table '{parent_table}'"
                )
                continue

            child_q = _quote_ident(child_table)
            parent_q = _quote_ident(parent_table)
            join_conds = " AND ".join(
                f"c.{_quote_ident(c_col)} = p.{_quote_ident(p_col)}"
                for c_col, p_col in zip(c_cols, p_cols)
            )
            not_null_conds = " AND ".join(
                f"c.{_quote_ident(c_col)} IS NOT NULL" for c_col in c_cols
            )
            null_parent_conds = " AND ".join(
                f"p.{_quote_ident(p_col)} IS NULL" for p_col in p_cols
            )

            check_sql = (
                f"SELECT COUNT(*) FROM {child_q} c "
                f"LEFT JOIN {parent_q} p ON {join_conds} "
                f"WHERE {not_null_conds} AND ({null_parent_conds})"
            )
            with target_engine.connect() as conn:
                try:
                    orphans = conn.execute(sa.text(check_sql)).scalar() or 0
                    if orphans > 0:
                        fk_violations += int(orphans)
                        fk_violation_details.append(
                            f"{orphans} orphaned row(s) in '{child_table}' referencing '{parent_table}'"
                        )
                except Exception as exc:
                    logger.debug("FK check between %s -> %s failed: %s", child_table, parent_table, exc)

    if initial_source_row_count is not None:
        source_unchanged = (source_total == initial_source_row_count)
    else:
        source_unchanged = (source_total == sum(_row_count(source_engine, t) for t in source_tables))

    report = ValidationReport(
        pk_unique=pk_unique,
        fk_violations=fk_violations,
        fk_violation_details=fk_violation_details,
        budget_compliant=budget_compliant,
        actual_rows=actual_rows,
        budget_limit=budget_limit,
        tables_present=target_tables,
        tables_missing=missing,
        source_unchanged=source_unchanged,
        schema_compatible=schema_compatible,
    )

    report.errors = []
    if not report.pk_unique:
        report.errors.append("Target table primary keys are not unique.")
    if report.fk_violations > 0:
        report.errors.append(f"Target table contains {report.fk_violations} foreign-key violation(s).")
    if not report.budget_compliant:
        report.errors.append(f"Target row count ({actual_rows}) exceeds the budget limit ({budget_limit}).")
    if report.tables_missing:
        report.errors.append(f"Missing expected tables: {report.tables_missing}")
    if not report.schema_compatible:
        report.errors.append("Target schema differs from the source schema.")
    if not report.source_unchanged:
        report.errors.append("Source database row count has been modified.")

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

        t_src = 0.0
        t_tgt = 0.0
        try:
            source_rows, t_src = _timed_query_rows(source_engine, query.raw_sql)
            target_rows, t_tgt = _timed_query_rows(target_engine, query.raw_sql)
        except Exception as exc:
            per_query_results.append(
                QueryResult(
                    query_id=query.query_id,
                    query_text=query.raw_sql,
                    source_row_count=query.source_row_count,
                    execution_time_source=t_src,
                    execution_time_target=t_tgt,
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
                # Result row retention (exact tuple overlap)
                common = set(source_norm) & set(target_norm)
                result_row_retention = len(common) / len(source_norm) if source_norm else 0.0

                # Key retention: match on ID/key columns if present, otherwise common rows
                key_cols = [
                    k for k in source_rows[0].keys()
                    if k.lower() in ("id", "emp_no", "dept_no", "pk", "key", "code")
                    or k.lower().endswith(("_id", "_no", "_code", "_key", "_pk"))
                ]
                if key_cols:
                    src_keys = {tuple(str(r.get(c)) for c in key_cols) for r in source_rows}
                    tgt_keys = {tuple(str(r.get(c)) for c in key_cols) for r in target_rows}
                    key_retention = len(src_keys & tgt_keys) / len(src_keys) if src_keys else 0.0
                else:
                    key_retention = result_row_retention

                # Aggregate retention: for GROUP BY queries, measure group key survival
                if query.group_columns or query.aggregate_functions:
                    group_col_names = [col_name for _, col_name in query.group_columns]
                    if not group_col_names and source_rows:
                        # Fallback to non-aggregate output columns
                        group_col_names = [
                            k for k in source_rows[0].keys()
                            if not any(agg.lower() in k.lower() for agg in ("count", "sum", "avg", "min", "max", "cnt"))
                        ]

                    if group_col_names:
                        src_groups = {tuple(str(r.get(c)) for c in group_col_names) for r in source_rows}
                        tgt_groups = {tuple(str(r.get(c)) for c in group_col_names) for r in target_rows}
                        aggregate_retention = len(src_groups & tgt_groups) / len(src_groups) if src_groups else 0.0
                    else:
                        aggregate_retention = 1.0 if target_rows else 0.0
                else:
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
            execution_time_source=t_src,
            execution_time_target=t_tgt,
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
