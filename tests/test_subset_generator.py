"""
Tests for safeslicesql.subset_generator module — Phase 3.

Covers:
- Subset materialization with safe insertion order (parents first)
- Foreign-key referential integrity preserved without disabling FK checks
- Source database immutability
- Preservation of original primary key values
- Hard row budget rejection
- Prevention of source database overwrite
- Transaction safety and clean target refresh
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.types import SelectionResult


@pytest.fixture
def source_engine():
    """Create a populated source in-memory SQLite database."""
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON;"))
        conn.execute(
            text(
                """
                CREATE TABLE departments (
                    dept_no VARCHAR(4) PRIMARY KEY,
                    dept_name VARCHAR(40) NOT NULL
                );
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE employees (
                    emp_no INTEGER PRIMARY KEY,
                    first_name VARCHAR(20) NOT NULL,
                    last_name VARCHAR(20) NOT NULL
                );
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE dept_emp (
                    id INTEGER PRIMARY KEY,
                    emp_no INTEGER NOT NULL,
                    dept_no VARCHAR(4) NOT NULL,
                    FOREIGN KEY (emp_no) REFERENCES employees(emp_no),
                    FOREIGN KEY (dept_no) REFERENCES departments(dept_no)
                );
                """
            )
        )
        # Seed source data
        conn.execute(
            text(
                "INSERT INTO departments VALUES ('d001', 'Marketing'), ('d002', 'Finance'), ('d003', 'Human Resources');"
            )
        )
        conn.execute(
            text(
                "INSERT INTO employees VALUES (101, 'Alice', 'Smith'), (102, 'Bob', 'Jones'), (103, 'Charlie', 'Brown');"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dept_emp VALUES (1, 101, 'd001'), (2, 102, 'd002'), (3, 103, 'd001');"
            )
        )
        conn.commit()

    yield engine
    engine.dispose()


@pytest.fixture
def target_engine():
    """Create an empty target SQLite database with strict foreign keys enabled."""
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON;"))
    yield engine
    engine.dispose()


class TestSubsetMaterializer:
    """Unit tests for materialize_subset."""

    def test_materialize_respects_parent_before_child(self, source_engine, target_engine):
        """
        Verify rows are inserted parents first so that child foreign keys succeed
        with foreign key checks strictly ENABLED on the target database.
        """
        schema = inspect_schema(source_engine)
        graph = build_dependency_graph(schema)

        # Select 2 departments, 2 employees, and 2 dept_emp rows
        selection = SelectionResult(
            method="test-method",
            seed=42,
            budget=10,
            selected_rows={
                "departments": ["d001", "d002"],
                "employees": [101, 102],
                "dept_emp": [1, 2],
            },
            total_selected=6,
        )

        res = materialize_subset(
            source_engine=source_engine,
            target_engine=target_engine,
            schema=schema,
            selection=selection,
            graph=graph,
            target_database="test_target_db",
        )

        assert res.success is True, f"Materialization failed: {res.error_message}"
        assert res.total_rows_inserted == 6
        assert res.rows_inserted["departments"] == 2
        assert res.rows_inserted["employees"] == 2
        assert res.rows_inserted["dept_emp"] == 2

        # Verify insertion order: parents (departments, employees) before child (dept_emp)
        assert res.insertion_order.index("dept_emp") > res.insertion_order.index("departments")
        assert res.insertion_order.index("dept_emp") > res.insertion_order.index("employees")

        # Verify data in target
        with target_engine.connect() as conn:
            d_count = conn.execute(text("SELECT count(*) FROM departments")).scalar()
            e_count = conn.execute(text("SELECT count(*) FROM employees")).scalar()
            de_count = conn.execute(text("SELECT count(*) FROM dept_emp")).scalar()
            assert d_count == 2
            assert e_count == 2
            assert de_count == 2

    def test_pk_values_preserved_exactly(self, source_engine, target_engine):
        """Selected rows must retain their exact original primary key values."""
        schema = inspect_schema(source_engine)
        graph = build_dependency_graph(schema)

        selection = SelectionResult(
            method="test",
            seed=1,
            budget=10,
            selected_rows={
                "employees": [102],
                "departments": ["d002"],
                "dept_emp": [2],
            },
            total_selected=3,
        )

        res = materialize_subset(
            source_engine=source_engine,
            target_engine=target_engine,
            schema=schema,
            selection=selection,
            graph=graph,
            target_database="target_db",
        )
        assert res.success is True

        with target_engine.connect() as conn:
            emp = conn.execute(text("SELECT emp_no, first_name FROM employees")).fetchone()
            assert emp[0] == 102
            assert emp[1] == "Bob"

            dept = conn.execute(text("SELECT dept_no, dept_name FROM departments")).fetchone()
            assert dept[0] == "d002"
            assert dept[1] == "Finance"

    def test_source_database_immutability(self, source_engine, target_engine):
        """Materialization must NOT alter or delete any source database data."""
        with source_engine.connect() as conn:
            before_d = conn.execute(text("SELECT count(*) FROM departments")).scalar()
            before_e = conn.execute(text("SELECT count(*) FROM employees")).scalar()
            before_de = conn.execute(text("SELECT count(*) FROM dept_emp")).scalar()

        schema = inspect_schema(source_engine)
        graph = build_dependency_graph(schema)

        selection = SelectionResult(
            method="test",
            seed=1,
            budget=10,
            selected_rows={
                "departments": ["d001"],
                "employees": [101],
                "dept_emp": [1],
            },
            total_selected=3,
        )

        materialize_subset(
            source_engine=source_engine,
            target_engine=target_engine,
            schema=schema,
            selection=selection,
            graph=graph,
            target_database="target_db",
        )

        with source_engine.connect() as conn:
            after_d = conn.execute(text("SELECT count(*) FROM departments")).scalar()
            after_e = conn.execute(text("SELECT count(*) FROM employees")).scalar()
            after_de = conn.execute(text("SELECT count(*) FROM dept_emp")).scalar()

        assert before_d == after_d == 3
        assert before_e == after_e == 3
        assert before_de == after_de == 3

    def test_hard_row_budget_rejection(self, source_engine, target_engine):
        """Materialization refuses to proceed if selected rows exceed budget."""
        schema = inspect_schema(source_engine)
        graph = build_dependency_graph(schema)

        selection = SelectionResult(
            method="test",
            seed=1,
            budget=2,  # Budget is 2
            selected_rows={
                "departments": ["d001", "d002"],
                "employees": [101],
            },
            total_selected=3,  # 3 rows > budget 2
        )

        res = materialize_subset(
            source_engine=source_engine,
            target_engine=target_engine,
            schema=schema,
            selection=selection,
            graph=graph,
            target_database="target_db",
        )

        assert res.success is False
        assert "exceeds hard budget" in res.error_message

    def test_prevent_source_overwrite_guard(self, source_engine):
        """Materialization raises ValueError if target matches source database name."""
        schema = inspect_schema(source_engine)
        graph = build_dependency_graph(schema)

        # Force matching name and host
        source_engine.url = source_engine.url.set(database="production_db")
        target_engine = sa.create_engine("sqlite:///:memory:")
        target_engine.url = target_engine.url.set(database="production_db")

        selection = SelectionResult(method="test", seed=1, budget=10, selected_rows={})

        with pytest.raises(ValueError, match="identical to the source database"):
            materialize_subset(
                source_engine=source_engine,
                target_engine=target_engine,
                schema=schema,
                selection=selection,
                graph=graph,
                target_database="production_db",
            )
        target_engine.dispose()

    def test_repeated_materialization_refreshes_cleanly(self, source_engine, target_engine):
        """Repeated runs refresh target tables without foreign key conflicts."""
        schema = inspect_schema(source_engine)
        graph = build_dependency_graph(schema)

        selection1 = SelectionResult(
            method="test",
            seed=1,
            budget=10,
            selected_rows={"departments": ["d001"]},
            total_selected=1,
        )
        res1 = materialize_subset(
            source_engine, target_engine, schema, selection1, graph, "target_db"
        )
        assert res1.success is True
        assert res1.total_rows_inserted == 1

        # Second run with different rows
        selection2 = SelectionResult(
            method="test",
            seed=2,
            budget=10,
            selected_rows={"departments": ["d002", "d003"]},
            total_selected=2,
        )
        res2 = materialize_subset(
            source_engine, target_engine, schema, selection2, graph, "target_db"
        )
        assert res2.success is True
        assert res2.total_rows_inserted == 2

        with target_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM departments")).scalar() == 2

    def test_empty_selection_creates_schema_with_zero_rows(self, source_engine, target_engine):
        """Empty selection creates schema on target with 0 rows inserted."""
        schema = inspect_schema(source_engine)
        graph = build_dependency_graph(schema)

        selection = SelectionResult(
            method="test",
            seed=42,
            budget=10,
            selected_rows={},
            total_selected=0,
        )

        res = materialize_subset(
            source_engine, target_engine, schema, selection, graph, "target_db"
        )
        assert res.success is True
        assert res.total_rows_inserted == 0
        assert set(res.tables_created) == {"departments", "employees", "dept_emp"}

    def test_cyclic_graph_materialization_fails(self, source_engine, target_engine):
        """Materialization fails if dependency graph has cycles."""
        schema = inspect_schema(source_engine)
        # Manually inject a cycle into schema
        from safeslicesql.types import ForeignKeyMetadata

        schema.tables["departments"].foreign_keys.append(
            ForeignKeyMetadata("fk_dept_cycle", "dept_no", "dept_emp", "dept_no")
        )
        graph = build_dependency_graph(schema)
        assert graph.has_cycles is True

        selection = SelectionResult(method="test", seed=1, budget=10, selected_rows={})
        res = materialize_subset(
            source_engine, target_engine, schema, selection, graph, "target_db"
        )

        assert res.success is False
        assert "cycles" in res.error_message.lower()

    def test_fallback_schema_creation_from_metadata(self, source_engine, target_engine):
        """Verify schema reproduction works using SchemaMetadata when reflection is bypassed."""
        from safeslicesql.subset_generator import _reproduce_schema_on_target

        schema = inspect_schema(source_engine)
        graph = build_dependency_graph(schema)
        order = graph.topological_order()

        # Pass a dummy/broken source engine to trigger the fallback to SchemaMetadata
        broken_engine = sa.create_engine("sqlite:///:memory:")
        meta = _reproduce_schema_on_target(broken_engine, target_engine, schema, order)
        broken_engine.dispose()

        assert "departments" in meta.tables
        assert "employees" in meta.tables
        assert "dept_emp" in meta.tables

    def test_materialize_composite_pk_table(self):
        """Tables with composite primary keys (e.g. dept_emp) materialize correctly."""
        source = sa.create_engine("sqlite:///:memory:")
        target = sa.create_engine("sqlite:///:memory:")

        with source.connect() as conn:
            conn.execute(
                text(
                    "CREATE TABLE departments (dept_no VARCHAR(4) PRIMARY KEY, dept_name VARCHAR(40));"
                )
            )
            conn.execute(
                text("CREATE TABLE employees (emp_no INTEGER PRIMARY KEY, name VARCHAR(40));")
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE dept_emp (
                        emp_no INTEGER NOT NULL,
                        dept_no VARCHAR(4) NOT NULL,
                        from_date VARCHAR(10),
                        PRIMARY KEY (emp_no, dept_no),
                        FOREIGN KEY (emp_no) REFERENCES employees(emp_no),
                        FOREIGN KEY (dept_no) REFERENCES departments(dept_no)
                    );
                    """
                )
            )
            conn.execute(
                text("INSERT INTO departments VALUES ('d001', 'Engineering'), ('d002', 'Sales');")
            )
            conn.execute(text("INSERT INTO employees VALUES (101, 'Ada'), (102, 'Grace');"))
            conn.execute(
                text(
                    "INSERT INTO dept_emp VALUES (101, 'd001', '2020-01-01'), (102, 'd002', '2021-01-01');"
                )
            )
            conn.commit()

        schema = inspect_schema(source)
        graph = build_dependency_graph(schema)

        selection = SelectionResult(
            method="test",
            seed=42,
            budget=10,
            selected_rows={
                "departments": {"d001"},
                "employees": {101},
                "dept_emp": {(101, "d001")},
            },
            total_selected=3,
        )

        res = materialize_subset(source, target, schema, selection, graph, "target_db")
        assert res.success is True
        assert res.total_rows_inserted == 3

        with target.connect() as conn:
            rows = conn.execute(text("SELECT emp_no, dept_no FROM dept_emp")).fetchall()
            assert rows == [(101, "d001")]
