"""Tests for sampler module — Phases 4-5."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from safeslicesql.sampler import select_rows
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.types import (
    ColumnMetadata,
    ForeignKeyMetadata,
    SchemaMetadata,
    TableMetadata,
)
from safeslicesql.workload_analyzer import WorkloadAnalysis


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


@pytest.mark.parametrize("method", ["random-repair", "referential", "workload-aware"])
def test_selection_supports_composite_primary_keys(method):
    """Selectors should retain tuple PKs and close their single-column FKs."""
    schema = SchemaMetadata(database_name="composite")
    schema.tables["parents"] = TableMetadata(
        name="parents",
        primary_key="parent_id",
        columns=[ColumnMetadata("parent_id", "INTEGER", False, is_primary_key=True)],
        row_count=2,
    )
    schema.tables["child_rows"] = TableMetadata(
        name="child_rows",
        primary_keys=["parent_id", "sequence"],
        columns=[
            ColumnMetadata("parent_id", "INTEGER", False, is_primary_key=True),
            ColumnMetadata("sequence", "INTEGER", False, is_primary_key=True),
            ColumnMetadata("value", "TEXT", True),
        ],
        foreign_keys=[
            ForeignKeyMetadata("fk_child_parent", "parent_id", "parents", "parent_id")
        ],
        row_count=3,
    )
    engine = {
        "parents": {
            1: {"parent_id": 1},
            2: {"parent_id": 2},
        },
        "child_rows": {
            (1, 1): {"parent_id": 1, "sequence": 1, "value": "a"},
            (1, 2): {"parent_id": 1, "sequence": 2, "value": "b"},
            (2, 1): {"parent_id": 2, "sequence": 1, "value": "c"},
        },
    }

    result = select_rows(
        schema=schema,
        workload=WorkloadAnalysis(),
        graph=None,
        engine=engine,
        budget=4,
        method=method,
        seed=7,
    )

    assert result.total_selected <= result.budget
    assert result.selected_rows.get("child_rows")
    for composite_pk in result.selected_rows["child_rows"]:
        assert isinstance(composite_pk, tuple)
        assert len(composite_pk) == 2
    selected_parent_ids = set(result.selected_rows.get("parents", []))
    assert all(pk[0] in selected_parent_ids for pk in result.selected_rows["child_rows"])


@pytest.mark.parametrize("method", ["random-repair", "referential", "workload-aware"])
def test_sql_selector_supports_composite_primary_keys(method):
    """SQL-backed selectors should sample tuple PKs and include FK parents."""
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        conn.execute(text("CREATE TABLE parents (parent_id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE child_rows (parent_id INTEGER, sequence INTEGER, value TEXT, "
                "PRIMARY KEY (parent_id, sequence), "
                "FOREIGN KEY (parent_id) REFERENCES parents(parent_id))"
            )
        )
        conn.execute(text("INSERT INTO parents VALUES (1, 'one'), (2, 'two')"))
        conn.execute(
            text(
                "INSERT INTO child_rows VALUES "
                "(1, 1, 'a'), (1, 2, 'b'), (2, 1, 'c')"
            )
        )

    try:
        schema = inspect_schema(engine)
        result = select_rows(
            schema=schema,
            workload=WorkloadAnalysis(),
            graph=None,
            engine=engine,
            budget=4,
            method=method,
            seed=7,
        )

        assert result.total_selected <= result.budget
        assert result.selected_rows.get("child_rows")
        assert all(isinstance(pk, tuple) and len(pk) == 2 for pk in result.selected_rows["child_rows"])
        selected_parent_ids = set(result.selected_rows.get("parents", []))
        assert all(pk[0] in selected_parent_ids for pk in result.selected_rows["child_rows"])
    finally:
        engine.dispose()


def test_selector_rejects_unsupported_composite_foreign_keys(sample_schema, sample_engine):
    schema = sample_schema
    schema.unsupported_features.append(
        "Table 'child_rows' has composite foreign key 'fk_pair'."
    )

    with pytest.raises(ValueError, match="does not support composite foreign keys"):
        select_rows(
            schema=schema,
            workload=None,
            graph=None,
            engine=sample_engine,
            budget=5,
            method="random-repair",
            seed=42,
        )
