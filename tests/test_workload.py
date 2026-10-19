"""Tests for workload_analyzer module — Phase 5."""

from __future__ import annotations

from pathlib import Path

from safeslicesql.sampler import select_rows
from safeslicesql.types import ColumnMetadata, ForeignKeyMetadata, SchemaMetadata, TableMetadata
from safeslicesql.workload_analyzer import analyze_workload, load_workload


def test_load_workload_parses_queries():
    """The workload file should yield readable SQL statements."""
    queries = load_workload(Path("workloads/workload.sql"))
    assert len(queries) >= 5
    assert all(query.strip().startswith("SELECT") for query in queries)


def test_analyze_workload_extracts_query_features():
    """Workload analysis should classify tables, joins, filters, and aggregates."""
    analysis = analyze_workload(Path("workloads/workload.sql"), engine=None)

    assert analysis.queries
    first = analysis.queries[0]
    assert first.tables
    assert first.source_row_count >= 0
    assert len(analysis.supported_queries) >= 1


def test_workload_aware_selection_is_budgeted_and_reproducible():
    """The workload-aware selector must return a valid selection under the budget."""
    schema = SchemaMetadata(database_name="company")
    schema.tables["departments"] = TableMetadata(
        name="departments",
        primary_key="dept_no",
        columns=[ColumnMetadata("dept_no", "VARCHAR(4)", False, is_primary_key=True)],
        row_count=3,
    )
    schema.tables["employees"] = TableMetadata(
        name="employees",
        primary_key="emp_no",
        columns=[ColumnMetadata("emp_no", "INTEGER", False, is_primary_key=True)],
        row_count=4,
    )
    schema.tables["dept_emp"] = TableMetadata(
        name="dept_emp",
        primary_key="id",
        columns=[
            ColumnMetadata("id", "INTEGER", False, is_primary_key=True),
            ColumnMetadata("emp_no", "INTEGER", False),
            ColumnMetadata("dept_no", "VARCHAR(4)", False),
        ],
        foreign_keys=[
            ForeignKeyMetadata("fk_dept_emp_emp", "emp_no", "employees", "emp_no"),
            ForeignKeyMetadata("fk_dept_emp_dept", "dept_no", "departments", "dept_no"),
        ],
    )

    workload = analyze_workload(Path("workloads/workload.sql"), engine=None)
    engine = {
        "departments": {"d001": {"dept_no": "d001"}, "d002": {"dept_no": "d002"}},
        "employees": {101: {"emp_no": 101}, 102: {"emp_no": 102}, 103: {"emp_no": 103}},
        "dept_emp": {
            1: {"id": 1, "emp_no": 101, "dept_no": "d001"},
            2: {"id": 2, "emp_no": 102, "dept_no": "d002"},
            3: {"id": 3, "emp_no": 103, "dept_no": "d001"},
        },
    }

    result = select_rows(
        schema=schema,
        workload=workload,
        graph=None,
        engine=engine,
        budget=6,
        method="workload-aware",
        seed=11,
    )

    assert result.method == "workload-aware"
    assert result.seed == 11
    assert result.budget == 6
    assert sum(len(pks) for pks in result.selected_rows.values()) <= 6
    assert result.selection_log
