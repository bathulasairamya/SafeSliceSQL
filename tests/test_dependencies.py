"""
Tests for safeslicesql.dependency_graph module — Phase 2.

Covers:
- Directed dependency graph construction (parent -> child edges)
- Parent and child lookup functions
- Cycle detection and unsupported graph structure reporting
- Topological sort (parent-first insertion order)
- Transitive dependency closure computation
- v1 Nullable FK policy (NULL = no dependency)
- Hard row budget enforcement
"""

from __future__ import annotations

import pytest

from safeslicesql.dependency_graph import (
    DependencyGraph,
    build_dependency_graph,
    compute_closure,
)
from safeslicesql.types import (
    ColumnMetadata,
    ForeignKeyMetadata,
    SchemaMetadata,
    TableMetadata,
)


@pytest.fixture
def sample_company_schema() -> SchemaMetadata:
    """
    Schema with 3 tables in a clean DAG:
    departments (parent)
    employees (parent)
    dept_emp (child referencing both departments and employees)
    """
    schema = SchemaMetadata(database_name="company")

    # departments: dept_no (PK)
    schema.tables["departments"] = TableMetadata(
        name="departments",
        primary_key="dept_no",
        row_count=2,
        columns=[ColumnMetadata("dept_no", "VARCHAR(4)", False, is_primary_key=True)],
    )

    # employees: emp_no (PK)
    schema.tables["employees"] = TableMetadata(
        name="employees",
        primary_key="emp_no",
        row_count=5,
        columns=[ColumnMetadata("emp_no", "INTEGER", False, is_primary_key=True)],
    )

    # dept_emp: id (PK), emp_no (FK -> employees), dept_no (FK -> departments)
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
def multi_tier_schema() -> SchemaMetadata:
    """
    Schema with 3-tier hierarchy:
    countries -> regions -> stores -> orders
    """
    schema = SchemaMetadata(database_name="retail")

    schema.tables["countries"] = TableMetadata(
        name="countries",
        primary_key="country_id",
        columns=[ColumnMetadata("country_id", "INTEGER", False, is_primary_key=True)],
    )

    schema.tables["regions"] = TableMetadata(
        name="regions",
        primary_key="region_id",
        columns=[ColumnMetadata("region_id", "INTEGER", False, is_primary_key=True)],
        foreign_keys=[
            ForeignKeyMetadata("fk_reg_country", "country_id", "countries", "country_id")
        ],
    )

    schema.tables["stores"] = TableMetadata(
        name="stores",
        primary_key="store_id",
        columns=[ColumnMetadata("store_id", "INTEGER", False, is_primary_key=True)],
        foreign_keys=[
            ForeignKeyMetadata("fk_store_reg", "region_id", "regions", "region_id")
        ],
    )

    return schema


class TestDependencyGraph:
    """Unit tests for DependencyGraph structure, lookups, and topological sort."""

    def test_graph_construction(self, sample_company_schema):
        """Graph builds correct nodes and parent -> child edges."""
        graph = build_dependency_graph(sample_company_schema)

        assert not graph.has_cycles
        assert set(graph.get_parents("dept_emp")) == {"departments", "employees"}
        assert graph.get_parents("employees") == []
        assert graph.get_parents("departments") == []

        assert graph.get_children("employees") == ["dept_emp"]
        assert graph.get_children("departments") == ["dept_emp"]
        assert graph.get_children("dept_emp") == []

    def test_topological_sort_order(self, sample_company_schema):
        """Topological order guarantees parents appear before children."""
        graph = build_dependency_graph(sample_company_schema)
        order = graph.topological_order()

        assert len(order) == 3
        # dept_emp must come after both departments and employees
        assert order.index("dept_emp") > order.index("employees")
        assert order.index("dept_emp") > order.index("departments")

    def test_multi_tier_topological_order(self, multi_tier_schema):
        """Deep hierarchy is topologically ordered correctly."""
        graph = build_dependency_graph(multi_tier_schema)
        order = graph.topological_order()

        assert order.index("countries") < order.index("regions")
        assert order.index("regions") < order.index("stores")

    def test_cycle_detection(self):
        """Cyclic schemas are detected and raise error on topological sort."""
        schema = SchemaMetadata(database_name="cyclic_db")

        # Table A references Table B
        schema.tables["table_a"] = TableMetadata(
            name="table_a",
            primary_key="id",
            foreign_keys=[
                ForeignKeyMetadata("fk_a_b", "b_id", "table_b", "id")
            ],
        )

        # Table B references Table A (Cycle!)
        schema.tables["table_b"] = TableMetadata(
            name="table_b",
            primary_key="id",
            foreign_keys=[
                ForeignKeyMetadata("fk_b_a", "a_id", "table_a", "id")
            ],
        )

        graph = build_dependency_graph(schema)
        assert graph.has_cycles is True
        assert len(graph.cycles()) >= 1

        with pytest.raises(ValueError, match="contains cycles"):
            graph.topological_order()


class TestDependencyClosure:
    """Unit tests for recursive dependency closure and budget enforcement."""

    def test_closure_no_foreign_keys(self, sample_company_schema):
        """Selecting rows with no FKs adds zero closure rows."""
        graph = build_dependency_graph(sample_company_schema)

        selected = {"departments": {"d001", "d002"}}
        closure = compute_closure(graph, selected, engine=None, budget=10)

        assert closure.is_feasible is True
        assert closure.total_rows == 2
        assert closure.total_closure_additions == 0
        assert closure.selected_rows["departments"] == {"d001", "d002"}

    def test_closure_pulls_parent_rows(self, sample_company_schema):
        """Selecting a child row brings in its required parent rows."""
        graph = build_dependency_graph(sample_company_schema)

        mock_data = {
            "dept_emp": {
                1: {"emp_no": 1001, "dept_no": "d001"},
            }
        }

        selected = {"dept_emp": {1}}
        closure = compute_closure(graph, selected, engine=mock_data, budget=10)

        assert closure.is_feasible is True
        assert closure.total_rows == 3  # dept_emp(1) + employee(1001) + department(d001)
        assert closure.total_closure_additions == 2
        assert closure.closure_additions["employees"] == {1001}
        assert closure.closure_additions["departments"] == {"d001"}
        assert closure.selected_rows["employees"] == {1001}
        assert closure.selected_rows["departments"] == {"d001"}

    def test_multi_level_transitive_closure(self, multi_tier_schema):
        """Grandchild row pulls in parent row which pulls in grandparent row."""
        graph = build_dependency_graph(multi_tier_schema)

        mock_data = {
            "stores": {
                50: {"region_id": 5},
            },
            "regions": {
                5: {"country_id": 1},
            },
            "countries": {
                1: {},
            },
        }

        selected = {"stores": {50}}
        closure = compute_closure(graph, selected, engine=mock_data, budget=10)

        assert closure.is_feasible is True
        assert closure.total_rows == 3  # store(50) + region(5) + country(1)
        assert closure.selected_rows["stores"] == {50}
        assert closure.selected_rows["regions"] == {5}
        assert closure.selected_rows["countries"] == {1}
        assert closure.closure_additions["regions"] == {5}
        assert closure.closure_additions["countries"] == {1}

    def test_nullable_foreign_key_policy(self, sample_company_schema):
        """v1 policy: NULL foreign key values require no parent rows."""
        graph = build_dependency_graph(sample_company_schema)

        # Child row with NULL dept_no
        mock_data = {
            "dept_emp": {
                2: {"emp_no": 1002, "dept_no": None},
            }
        }

        selected = {"dept_emp": {2}}
        closure = compute_closure(graph, selected, engine=mock_data, budget=10)

        assert closure.is_feasible is True
        # Only pulls employee(1002), NOT department
        assert closure.total_rows == 2
        assert closure.selected_rows["employees"] == {1002}
        assert closure.selected_rows["departments"] == set()

    def test_budget_exceeded_marks_infeasible(self, sample_company_schema):
        """Closure exceeding hard budget is marked infeasible."""
        graph = build_dependency_graph(sample_company_schema)

        mock_data = {
            "dept_emp": {
                1: {"emp_no": 1001, "dept_no": "d001"},
            }
        }

        # Budget of 2, but closure requires 3 rows total (1 child + 2 parents)
        selected = {"dept_emp": {1}}
        closure = compute_closure(graph, selected, engine=mock_data, budget=2)

        assert closure.is_feasible is False
        assert "exceeds hard budget of 2" in closure.infeasibility_reason

    def test_existing_selection_not_recounted_in_additions(self, sample_company_schema):
        """If a parent row was already in the selection, it is not re-added."""
        graph = build_dependency_graph(sample_company_schema)

        mock_data = {
            "dept_emp": {
                1: {"emp_no": 1001, "dept_no": "d001"},
            }
        }

        # Parent 1001 is ALREADY selected
        selected = {
            "dept_emp": {1},
            "employees": {1001},
        }
        closure = compute_closure(graph, selected, engine=mock_data, budget=10)

        assert closure.is_feasible is True
        assert closure.total_rows == 3  # dept_emp(1), employees(1001), departments(d001)
        # Only departments(d001) was added by closure
        assert closure.total_closure_additions == 1
        assert closure.closure_additions["departments"] == {"d001"}
        assert closure.closure_additions["employees"] == set()

    def test_batch_fk_resolution_with_list_data(self, sample_company_schema):
        """In-memory list data is indexed and queried in O(1) batch lookup."""
        from safeslicesql.dependency_graph import _resolve_fks_batch

        mock_list_data = {
            "dept_emp": [
                {"id": 1, "emp_no": 1001, "dept_no": "d001"},
                {"id": 2, "emp_no": 1002, "dept_no": "d002"},
            ]
        }
        res = _resolve_fks_batch(
            engine=mock_list_data,
            table_name="dept_emp",
            pk_columns=["id"],
            pk_values=[1, 2],
            fk_columns=["emp_no", "dept_no"],
        )
        assert res[1] == {"emp_no": 1001, "dept_no": "d001"}
        assert res[2] == {"emp_no": 1002, "dept_no": "d002"}

    def test_self_referential_table_does_not_break_topological_sort(self):
        """Self-referential foreign keys (e.g. manager_id) do not crash topological sort."""
        schema = SchemaMetadata(database_name="corp")
        schema.tables["employees"] = TableMetadata(
            name="employees",
            primary_key="emp_no",
            columns=[
                ColumnMetadata("emp_no", "INTEGER", False, is_primary_key=True),
                ColumnMetadata("manager_id", "INTEGER", True),
            ],
            foreign_keys=[
                ForeignKeyMetadata("fk_emp_mgr", "manager_id", "employees", "emp_no")
            ],
        )

        graph = build_dependency_graph(schema)
        assert graph.has_cycles is False  # No cross-table cycles
        assert "employees" in graph.self_referential_tables
        assert graph.topological_order() == ["employees"]

    def test_database_resolution_error_propagation(self):
        """SQL error during batch resolution raises DatabaseResolutionError."""
        import sqlalchemy as sa
        from safeslicesql.types import DatabaseResolutionError
        from safeslicesql.dependency_graph import _resolve_fks_batch

        engine = sa.create_engine("sqlite:///:memory:")
        with pytest.raises(DatabaseResolutionError):
            # Query non-existent table to trigger DB error
            _resolve_fks_batch(
                engine=engine,
                table_name="non_existent_table",
                pk_columns=["id"],
                pk_values=[1, 2],
                fk_columns=["fk_col"],
            )

    def test_closure_resolves_fks_for_composite_pk_table_on_live_engine(self):
        """Transitive closure resolves outgoing FKs on composite PK child tables via live DB engine."""
        import sqlalchemy as sa
        from sqlalchemy import text
        from safeslicesql.schema_analyzer import inspect_schema

        engine = sa.create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE departments (dept_no VARCHAR(4) PRIMARY KEY, dept_name VARCHAR(40));"))
            conn.execute(text("CREATE TABLE employees (emp_no INTEGER PRIMARY KEY, name VARCHAR(40));"))
            conn.execute(text("""
                CREATE TABLE dept_emp (
                    emp_no INTEGER NOT NULL,
                    dept_no VARCHAR(4) NOT NULL,
                    PRIMARY KEY (emp_no, dept_no),
                    FOREIGN KEY (emp_no) REFERENCES employees(emp_no),
                    FOREIGN KEY (dept_no) REFERENCES departments(dept_no)
                );
            """))
            conn.execute(text("INSERT INTO departments VALUES ('d001', 'Engineering'), ('d002', 'Finance');"))
            conn.execute(text("INSERT INTO employees VALUES (101, 'Ada'), (102, 'Grace');"))
            conn.execute(text("INSERT INTO dept_emp VALUES (101, 'd001'), (102, 'd002');"))

        schema = inspect_schema(engine)
        graph = build_dependency_graph(schema)

        # Select only child composite PK (101, 'd001')
        selected = {"dept_emp": {(101, "d001")}}
        closure = compute_closure(graph, selected, engine=engine, budget=10)

        assert closure.is_feasible is True
        assert closure.total_rows == 3
        assert closure.selected_rows["dept_emp"] == {(101, "d001")}
        assert closure.selected_rows["employees"] == {101}
        assert closure.selected_rows["departments"] == {"d001"}
        assert closure.closure_additions["employees"] == {101}
        assert closure.closure_additions["departments"] == {"d001"}

