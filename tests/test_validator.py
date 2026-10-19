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
