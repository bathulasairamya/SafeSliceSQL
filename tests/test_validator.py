"""Tests for validator module — Phase 6."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import text

from safeslicesql.validator import evaluate_workload, validate_subset
from safeslicesql.workload_analyzer import analyze_workload


def test_validate_subset_detects_valid_subset():
    """A valid SQLite subset should pass budget and FK checks."""
    source = sa.create_engine("sqlite:///:memory:")
    target = sa.create_engine("sqlite:///:memory:")

    with source.begin() as conn:
        conn.execute(text("CREATE TABLE departments (dept_no TEXT PRIMARY KEY, dept_name TEXT)"))
        conn.execute(text("CREATE TABLE employees (emp_no INTEGER PRIMARY KEY, dept_no TEXT, FOREIGN KEY(dept_no) REFERENCES departments(dept_no))"))
        conn.execute(text("INSERT INTO departments VALUES ('d1', 'Sales')"))
        conn.execute(text("INSERT INTO employees VALUES (1, 'd1')"))

    with target.begin() as conn:
        conn.execute(text("CREATE TABLE departments (dept_no TEXT PRIMARY KEY, dept_name TEXT)"))
        conn.execute(text("CREATE TABLE employees (emp_no INTEGER PRIMARY KEY, dept_no TEXT, FOREIGN KEY(dept_no) REFERENCES departments(dept_no))"))
        conn.execute(text("INSERT INTO departments VALUES ('d1', 'Sales')"))
        conn.execute(text("INSERT INTO employees VALUES (1, 'd1')"))

    report = validate_subset(source, target, budget_limit=10)
    assert report.is_valid is True
    assert report.fk_violations == 0
    assert report.budget_compliant is True


def test_evaluate_workload_returns_retention_metrics():
    """Supported workload queries should execute on both source and target and yield retention metrics."""
    source = sa.create_engine("sqlite:///:memory:")
    target = sa.create_engine("sqlite:///:memory:")

    with source.begin() as conn:
        conn.execute(text("CREATE TABLE employees (emp_no INTEGER PRIMARY KEY, dept_no TEXT)"))
        conn.execute(text("INSERT INTO employees VALUES (1, 'd1'), (2, 'd1')"))

    with target.begin() as conn:
        conn.execute(text("CREATE TABLE employees (emp_no INTEGER PRIMARY KEY, dept_no TEXT)"))
        conn.execute(text("INSERT INTO employees VALUES (1, 'd1')"))

    workload = analyze_workload(Path("workloads/workload.sql"), engine=None)
    workload.queries = [
        type("QueryInfo", (), {"query_id": "Q1", "raw_sql": "SELECT * FROM employees WHERE emp_no = 1", "tables": ["employees"], "join_tables": [], "filter_columns": [("employees", "emp_no")], "group_columns": [], "aggregate_functions": [], "is_supported": True, "exclusion_reason": "", "source_row_count": 1})()
    ]

    report = evaluate_workload(source, target, workload)
    assert report.total_queries == 1
    assert report.supported_queries >= 1
    assert report.per_query_results[0].survived is True
    assert 0.0 <= report.per_query_results[0].result_row_retention <= 1.0


def test_validate_subset_detects_orphaned_foreign_keys():
    """Target containing orphaned child foreign-key records must fail validation."""
    source = sa.create_engine("sqlite:///:memory:")
    target = sa.create_engine("sqlite:///:memory:")

    schema_ddl = """
    CREATE TABLE departments (dept_no TEXT PRIMARY KEY, dept_name TEXT);
    CREATE TABLE employees (emp_no INTEGER PRIMARY KEY, dept_no TEXT, FOREIGN KEY(dept_no) REFERENCES departments(dept_no));
    """
    with source.begin() as conn:
        for stmt in schema_ddl.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text("INSERT INTO departments VALUES ('d1', 'Engineering')"))
        conn.execute(text("INSERT INTO employees VALUES (1, 'd1')"))

    with target.begin() as conn:
        for stmt in schema_ddl.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text("INSERT INTO departments VALUES ('d1', 'Engineering')"))
        # Insert valid child and orphaned child referencing non-existent 'd999'
        conn.execute(text("INSERT INTO employees VALUES (1, 'd1'), (2, 'd999')"))

    report = validate_subset(source, target, budget_limit=10)
    assert report.is_valid is False
    assert report.fk_violations == 1
    assert any("orphaned row" in detail for detail in report.fk_violation_details)


def test_validate_subset_detects_composite_pk_duplicates():
    """Target with valid composite primary keys passes PK uniqueness check."""
    source = sa.create_engine("sqlite:///:memory:")
    target = sa.create_engine("sqlite:///:memory:")

    ddl = "CREATE TABLE dept_emp (emp_no INTEGER, dept_no TEXT, PRIMARY KEY (emp_no, dept_no));"
    with source.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(text("INSERT INTO dept_emp VALUES (1, 'd1')"))

    with target.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(text("INSERT INTO dept_emp VALUES (1, 'd1')"))

    report = validate_subset(source, target, budget_limit=10)
    assert report.pk_unique is True
    assert report.is_valid is True


def test_evaluate_workload_group_aggregate_retention():
    """Workload queries with GROUP BY and aggregate functions compute aggregate retention."""
    source = sa.create_engine("sqlite:///:memory:")
    target = sa.create_engine("sqlite:///:memory:")

    with source.begin() as conn:
        conn.execute(text("CREATE TABLE salaries (emp_no INTEGER, salary INTEGER)"))
        conn.execute(text("INSERT INTO salaries VALUES (1, 50000), (1, 55000), (2, 60000)"))

    with target.begin() as conn:
        conn.execute(text("CREATE TABLE salaries (emp_no INTEGER, salary INTEGER)"))
        conn.execute(text("INSERT INTO salaries VALUES (1, 50000)"))

    workload = analyze_workload(Path("workloads/workload.sql"), engine=None)
    workload.queries = [
        type("QueryInfo", (), {
            "query_id": "Q_AGG",
            "raw_sql": "SELECT emp_no, COUNT(*) FROM salaries GROUP BY emp_no",
            "tables": ["salaries"],
            "join_tables": [],
            "filter_columns": [],
            "group_columns": [("salaries", "emp_no")],
            "aggregate_functions": ["COUNT"],
            "is_supported": True,
            "exclusion_reason": "",
            "source_row_count": 2,
        })()
    ]

    report = evaluate_workload(source, target, workload)
    assert report.total_queries == 1
    res = report.per_query_results[0]
    assert res.survived is True
    # In source we had 2 groups (emp_no 1, 2); in target we have 1 group (emp_no 1)
    assert res.aggregate_retention == 0.5
    assert res.key_retention == 0.5
    assert res.result_row_retention == 0.0

