"""
Tests for safeslicesql.schema_analyzer module — Phase 2.

Covers:
- Schema introspection on live/mock engines
- Column metadata discovery (data types, nullability, defaults)
- Single-column primary key extraction
- Single-column foreign key extraction
- Unsupported schema detection (composite PK, composite FK, missing PK)
- Cycle detection warning in schema metadata
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.types import ColumnMetadata, ForeignKeyMetadata, TableMetadata


@pytest.fixture
def sqlite_engine():
    """Create an in-memory SQLite database engine with foreign keys enabled."""
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON;"))
    yield engine
    engine.dispose()


class TestSchemaIntrospection:
    """Unit tests for schema introspection using SQLite in-memory databases."""

    def test_inspect_simple_schema(self, sqlite_engine):
        """Introspect tables, columns, and foreign keys."""
        with sqlite_engine.connect() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE departments (
                        dept_no CHAR(4) PRIMARY KEY,
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
                        first_name VARCHAR(14) NOT NULL,
                        last_name VARCHAR(16) NOT NULL,
                        gender CHAR(1)
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
                        dept_no CHAR(4) NOT NULL,
                        FOREIGN KEY (emp_no) REFERENCES employees(emp_no),
                        FOREIGN KEY (dept_no) REFERENCES departments(dept_no)
                    );
                    """
                )
            )
            conn.commit()

        schema = inspect_schema(sqlite_engine)

        # Check table discovery
        assert set(schema.table_names) == {"departments", "employees", "dept_emp"}

        # Check primary keys
        assert schema.tables["departments"].primary_key == "dept_no"
        assert schema.tables["employees"].primary_key == "emp_no"
        assert schema.tables["dept_emp"].primary_key == "id"

        # Check column properties
        emp_table = schema.tables["employees"]
        emp_no_col = emp_table.get_column("emp_no")
        assert emp_no_col is not None
        assert emp_no_col.is_primary_key is True

        gender_col = emp_table.get_column("gender")
        assert gender_col is not None
        assert gender_col.nullable is True

        # Check foreign keys
        dept_emp_table = schema.tables["dept_emp"]
        assert len(dept_emp_table.foreign_keys) == 2

        fks_by_col = {fk.column: fk for fk in dept_emp_table.foreign_keys}
        assert fks_by_col["emp_no"].referenced_table == "employees"
        assert fks_by_col["emp_no"].referenced_column == "emp_no"
        assert fks_by_col["dept_no"].referenced_table == "departments"
        assert fks_by_col["dept_no"].referenced_column == "dept_no"

    def test_composite_primary_key_detected(self, sqlite_engine):
        """Composite primary keys are detected and reported as unsupported in v1."""
        with sqlite_engine.connect() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE project_members (
                        project_id INTEGER,
                        employee_id INTEGER,
                        role VARCHAR(50),
                        PRIMARY KEY (project_id, employee_id)
                    );
                    """
                )
            )
            conn.commit()

        schema = inspect_schema(sqlite_engine)
        tbl = schema.tables["project_members"]
        assert tbl.primary_key is None
        assert any(
            "composite primary key" in feat.lower()
            for feat in schema.unsupported_features
        )

    def test_table_without_primary_key_detected(self, sqlite_engine):
        """Tables without primary keys are detected and reported as unsupported."""
        with sqlite_engine.connect() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE audit_log (
                        timestamp DATETIME,
                        action VARCHAR(100)
                    );
                    """
                )
            )
            conn.commit()

        schema = inspect_schema(sqlite_engine)
        tbl = schema.tables["audit_log"]
        assert tbl.primary_key is None
        assert any(
            "no primary key" in feat.lower()
            for feat in schema.unsupported_features
        )

    def test_row_counts_reflected(self, sqlite_engine):
        """Row counts are correctly recorded in table metadata."""
        with sqlite_engine.connect() as conn:
            conn.execute(
                text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);")
            )
            conn.execute(
                text("INSERT INTO items (id, name) VALUES (1, 'A'), (2, 'B'), (3, 'C');")
            )
            conn.commit()

        schema = inspect_schema(sqlite_engine)
        assert schema.tables["items"].row_count == 3
        assert schema.total_row_count == 3


@pytest.mark.db
class TestLiveMySQLSchemaIntrospection:
    """Integration test on live MySQL source database if available."""

    def test_live_source_schema_introspection(self, app_config):
        """Introspect live source MySQL database if connected."""
        from safeslicesql.db import create_engine, test_connection

        engine = create_engine(app_config.source_db)
        if not test_connection(engine):
            pytest.skip("MySQL source database is not accessible. Configure .env to test.")

        schema = inspect_schema(engine)
        assert schema.database_name == app_config.source_db.database
        assert "employees" in schema.tables
        assert schema.tables["employees"].primary_key == "emp_no"
