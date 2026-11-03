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

"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import Engine

from safeslicesql.db import quote_ident
from safeslicesql.result_store import ResultStore, encode_values, temporary_result_store
from safeslicesql.schema_compatibility import table_schema_differences
from safeslicesql.source_snapshot import SourceSnapshot, capture_source_snapshot
from safeslicesql.types import (
    QueryResult,
    ValidationReport,
    WorkloadReport,
)
from safeslicesql.workload_analyzer import (
    WorkloadAnalysis,
    _extract_tables_and_aliases,
    _group_features,
)

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


@dataclass
class QueryMeasurement:
    columns: tuple[str, ...]
    row_count: int
    has_keys: bool
    elapsed: float


def _stream_query(
    engine: Engine,
    sql: str,
    store: ResultStore,
    *,
    source: bool,
    group_positions: list[int] | None,
    expected_columns: tuple[str, ...] | None = None,
) -> QueryMeasurement:
    """Stream a query into an indexed temporary store, keeping at most one batch."""
    started = time.perf_counter()
    with engine.connect() as connection:
        result = connection.execution_options(
            no_parameters=True, stream_results=True, max_row_buffer=1000
        ).exec_driver_sql(sql)
        try:
            columns = tuple(result.keys())
            if expected_columns is not None and columns != expected_columns:
                raise ValueError("Source and target query output columns differ.")
            key_positions = [
                i
                for i, label in enumerate(columns)
                if label.lower() in ("id", "emp_no", "dept_no", "pk", "key", "code")
                or label.lower().endswith(("_id", "_no", "_code", "_key", "_pk"))
            ]
            batch = []
            count = 0
            for row in result:
                batch.append((0, encode_values(row), int(source), int(not source)))
                for kind, positions in ((1, key_positions), (2, group_positions)):
                    if positions:
                        batch.append(
                            (
                                kind,
                                encode_values(row[i] for i in positions),
                                int(source),
                                int(not source),
                            )
                        )
                count += 1
                if len(batch) >= 1000:
                    store.add_batch(batch)
                    batch.clear()
            if batch:
                store.add_batch(batch)
            return QueryMeasurement(
                columns, count, bool(key_positions), time.perf_counter() - started
            )
        finally:
            result.close()


def validate_subset(
    source_engine: Engine,
    target_engine: Engine,
    budget_limit: int,
    initial_source_row_count: int | None = None,
    initial_source_snapshot: SourceSnapshot | None = None,
) -> ValidationReport:
    """Validate the subset, failing closed if any required check cannot complete."""
    try:
        return _validate_subset(
            source_engine,
            target_engine,
            budget_limit,
            initial_source_row_count,
            initial_source_snapshot,
        )
    except Exception as exc:
        logger.error("Subset validation could not complete: %s", exc)
        return ValidationReport(
            budget_limit=budget_limit,
            errors=[f"Subset validation could not complete: {exc}"],
        )


def _validate_subset(
    source_engine: Engine,
    target_engine: Engine,
    budget_limit: int,
    initial_source_row_count: int | None = None,
    initial_source_snapshot: SourceSnapshot | None = None,
) -> ValidationReport:
    """
    Validate a generated subset for correctness across any SQLAlchemy engine.

    Checks:
    - Target tables presence and column schema compatibility
    - Primary-key uniqueness across all primary key columns
    - Foreign-key referential integrity (0 orphaned references)
    - Hard row budget compliance
    - Source contents unchanged when a before-run snapshot is supplied
      (a row count alone can prove a change, but cannot prove immutability)
    """
    source_tables = _get_table_names(source_engine)
    target_tables = _get_table_names(target_engine)
    missing = [table for table in source_tables if table not in target_tables]

    pk_unique = True
    fk_violations = 0
    fk_violation_details: list[str] = []
    actual_rows = 0
    check_errors: list[str] = []

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
        if table_name in source_tables:
            # Check the source's expected key even if its target constraint was lost.
            pk_cols = (
                source_inspector.get_pk_constraint(table_name).get("constrained_columns", [])
                or pk_cols
            )
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
                    pk_unique = False
                    check_errors.append(f"PK check on '{table_name}' failed: {exc}")

        # 2. Check column schema compatibility
        if table_name in source_tables:
            differences = table_schema_differences(source_inspector, target_inspector, table_name)
            if differences:
                schema_compatible = False
                check_errors.extend(differences)

    # 3. Real foreign-key integrity check: look for orphaned child records
    for child_table in target_tables:
        inspector = source_inspector if child_table in source_tables else target_inspector
        fks = inspector.get_foreign_keys(child_table)
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
            null_parent_conds = " AND ".join(f"p.{_quote_ident(p_col)} IS NULL" for p_col in p_cols)

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
                    check_errors.append(
                        f"FK check between '{child_table}' -> '{parent_table}' failed: {exc}"
                    )

    source_unchanged = None
    if initial_source_snapshot is not None:
        source_unchanged = capture_source_snapshot(source_engine) == initial_source_snapshot
    elif initial_source_row_count is not None and source_total != initial_source_row_count:
        source_unchanged = False

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

    report.errors = check_errors
    if not report.pk_unique:
        report.errors.append("Target table primary keys are not unique.")
    if report.fk_violations > 0:
        report.errors.append(
            f"Target table contains {report.fk_violations} foreign-key violation(s)."
        )
    if not report.budget_compliant:
        report.errors.append(
            f"Target row count ({actual_rows}) exceeds the budget limit ({budget_limit})."
        )
    if report.tables_missing:
        report.errors.append(f"Missing expected tables: {report.tables_missing}")
    if not report.schema_compatible:
        report.errors.append("Target schema differs from the source schema.")
    if report.source_unchanged is False:
        report.errors.append("Source database contents or schema changed since the baseline.")

    return report


def evaluate_workload(
    source_engine: Engine,
    target_engine: Engine,
    workload: WorkloadAnalysis,
) -> WorkloadReport:
    """Evaluate supported queries; execution failures remain failed measurements."""
    results = []
    for query in workload.queries:
        result = QueryResult(query_id=query.query_id, query_text=query.raw_sql)
        if not query.is_supported:
            result.excluded = True
            result.exclusion_reason = query.exclusion_reason
            results.append(result)
            continue
        result.source_row_count = query.source_row_count
        result.source_result_known = bool(
            getattr(query, "source_evaluated", False) or query.source_row_count > 0
        )
        result.source_empty = result.source_result_known and result.source_row_count == 0
        try:
            positions = getattr(query, "group_output_positions", None)
            if positions is None and (query.group_columns or query.aggregate_functions):
                tables, aliases = _extract_tables_and_aliases(query.raw_sql)
                _, positions = _group_features(query.raw_sql, aliases, tables)
            with temporary_result_store() as store:
                source = _stream_query(
                    source_engine, query.raw_sql, store, source=True, group_positions=positions
                )
                result.execution_time_source = source.elapsed
                result.source_result_known = True
                result.source_row_count = source.row_count
                result.source_empty = source.row_count == 0
                target = _stream_query(
                    target_engine,
                    query.raw_sql,
                    store,
                    source=False,
                    group_positions=positions,
                    expected_columns=source.columns,
                )
                result.execution_time_target = target.elapsed
                result.target_row_count = target.row_count
                result.target_empty = target.row_count == 0
                result.survived = bool(source.row_count and target.row_count)
                if source.row_count:
                    result.result_row_retention = store.retention(0, multiset=True)
                    result.key_retention = (
                        store.retention(1) if source.has_keys else result.result_row_retention
                    )
                    # Unprojected groups and scalar aggregates use exact row retention.
                    result.aggregate_retention = (
                        store.retention(2) if positions else result.result_row_retention
                    )
                elif not target.row_count:
                    result.key_retention = result.result_row_retention = (
                        result.aggregate_retention
                    ) = 1.0

        except Exception as exc:
            result.error = f"Execution/evaluation failed: {exc}"
            result.survived = False
            result.key_retention = result.result_row_retention = result.aggregate_retention = 0.0
        results.append(result)

    supported = [result for result in results if not result.excluded]
    nonempty = sum(
        result.source_result_known and result.source_row_count > 0 for result in supported
    )
    unknown = sum(not result.source_result_known for result in supported)
    denominator = nonempty + unknown
    count = len(supported)
    return WorkloadReport(
        per_query_results=results,
        total_queries=len(results),
        supported_queries=count,
        excluded_queries=len(results) - count,
        failed_queries=sum(bool(result.error) for result in supported),
        source_nonempty_queries=nonempty,
        source_unknown_queries=unknown,
        survival_denominator=denominator,
        query_survival_rate=sum(result.survived for result in supported) / denominator
        if denominator
        else 0.0,
        overall_key_retention=sum(result.key_retention for result in supported) / count
        if count
        else 0.0,
        overall_result_retention=sum(result.result_row_retention for result in supported) / count
        if count
        else 0.0,
        overall_aggregate_retention=sum(result.aggregate_retention for result in supported) / count
        if count
        else 0.0,
    )
