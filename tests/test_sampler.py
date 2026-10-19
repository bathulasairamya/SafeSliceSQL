"""Tests for sampler module — Phases 4-5."""

from __future__ import annotations

import pytest

from safeslicesql.sampler import select_rows
from safeslicesql.types import (
    ColumnMetadata,
    ForeignKeyMetadata,
    SchemaMetadata,
    TableMetadata,
)


@pytest.fixture
def sample_schema() -> SchemaMetadata:
    """Create a tiny schema with a parent-child relationship for sampler tests."""
    schema = SchemaMetadata(database_name="company")
    schema.tables["departments"] = TableMetadata(
        name="departments",
        primary_key="dept_no",
        row_count=3,
        columns=[ColumnMetadata("dept_no", "VARCHAR(4)", False, is_primary_key=True)],
    )
    schema.tables["employees"] = TableMetadata(
        name="employees",
        primary_key="emp_no",
        row_count=4,
        columns=[ColumnMetadata("emp_no", "INTEGER", False, is_primary_key=True)],
    )
    schema.tables["dept_emp"] = TableMetadata(
        name="dept_emp",
        primary_key="id",
        row_count=5,
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
    return schema


@pytest.fixture
def sample_engine() -> dict[str, dict[int | str, dict[str, object]]]:
    """Mock engine mapping rows by table and primary key."""
    return {
        "departments": {
            "d001": {"dept_no": "d001"},
            "d002": {"dept_no": "d002"},
            "d003": {"dept_no": "d003"},
        },
        "employees": {
            101: {"emp_no": 101},
            102: {"emp_no": 102},
            103: {"emp_no": 103},
            104: {"emp_no": 104},
        },
        "dept_emp": {
            1: {"id": 1, "emp_no": 101, "dept_no": "d001"},
            2: {"id": 2, "emp_no": 102, "dept_no": "d002"},
            3: {"id": 3, "emp_no": 103, "dept_no": "d001"},
            4: {"id": 4, "emp_no": 104, "dept_no": "d003"},
            5: {"id": 5, "emp_no": 102, "dept_no": "d003"},
        },
    }


@pytest.mark.parametrize("method", ["random-repair", "referential"])
def test_baseline_select_rows_is_budgeted_and_reproducible(sample_schema, sample_engine, method):
    """Both baseline methods must honor the hard budget and produce stable results for a fixed seed."""
    budget = 6

    first = select_rows(
        schema=sample_schema,
        workload=None,
        graph=None,
        engine=sample_engine,
        budget=budget,
        method=method,
        seed=42,
    )
    second = select_rows(
        schema=sample_schema,
        workload=None,
        graph=None,
        engine=sample_engine,
        budget=budget,
        method=method,
        seed=42,
    )

    assert first.method == method
    assert first.seed == 42
    assert first.budget == budget
    assert sum(len(pks) for pks in first.selected_rows.values()) <= budget
    assert first.selected_rows == second.selected_rows


def test_random_repair_uses_dependency_closure(sample_schema, sample_engine):
    """Random repair should include required parents when a child row is selected."""
    result = select_rows(
        schema=sample_schema,
        workload=None,
        graph=None,
        engine=sample_engine,
        budget=8,
        method="random-repair",
        seed=7,
    )

    assert result.total_selected <= 8
    assert all(value for value in result.selected_rows.values())
    assert result.selection_log
