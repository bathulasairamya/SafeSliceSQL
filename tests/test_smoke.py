"""
Phase 1 Smoke Tests — SafeSliceSQL exit criteria.

These tests verify that the foundational setup is complete and working.
Every team member must pass these tests from a clean environment.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _get_safeslice_cmd() -> list[str]:
    """Resolve the safeslice CLI command cross-platform."""
    # Check within the current python environment (venv Scripts or bin)
    venv_exe = Path(sys.executable).parent / (
        "safeslice.exe" if sys.platform == "win32" else "safeslice"
    )
    if venv_exe.exists():
        return [str(venv_exe)]
    cmd = shutil.which("safeslice")
    if cmd:
        return [cmd]
    return [sys.executable, "-m", "safeslicesql.cli"]


def _get_env_with_pythonpath() -> dict[str, str]:
    """Return environment dictionary with src/ directory included in PYTHONPATH."""
    import os
    env = dict(os.environ)
    src_dir = str(Path(__file__).resolve().parent.parent / "src")
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else src_dir
    return env


class TestConfigLoads:
    """Configuration loading tests."""

    def test_config_loads_without_error(self):
        """Config loads from .env without errors."""
        from safeslicesql.config import load_config

        config = load_config()
        assert config.source_db.database, "Source database name must be set"
        assert config.target_db.database, "Target database name must be set"

    def test_source_target_differ(self):
        """Source and target database names must be different."""
        from safeslicesql.config import load_config

        config = load_config()
        assert config.source_db.database != config.target_db.database, (
            "Source and target databases must differ"
        )


@pytest.mark.db
class TestDatabaseConnection:
    """Database connectivity tests."""

    def test_source_connection(self, app_config):
        """Can connect to the source MySQL database."""
        from safeslicesql.db import create_engine, test_connection

        engine = create_engine(app_config.source_db)
        if not test_connection(engine):
            pytest.skip(
                f"Cannot connect to source database '{app_config.source_db.database}' "
                f"at {app_config.source_db.host}:{app_config.source_db.port}. "
                "Ensure MySQL is running and set valid credentials in .env."
            )
        assert True

    def test_employees_tables_exist(self, app_config):
        """Employees sample database has the expected tables."""
        from sqlalchemy import text

        from safeslicesql.db import create_engine, test_connection

        engine = create_engine(app_config.source_db)
        if not test_connection(engine):
            pytest.skip("MySQL connection not available.")

        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = :db"),
                {"db": app_config.source_db.database},
            )
            tables = {row[0] for row in result}

        expected = {
            "employees",
            "departments",
            "dept_emp",
            "dept_manager",
            "titles",
            "salaries",
        }
        missing = expected - tables
        assert not missing, f"Missing tables in source database: {missing}. Found: {tables}"

    def test_employees_has_rows(self, app_config):
        """Employees database tables contain data."""
        from safeslicesql.db import create_engine, get_row_count, test_connection

        engine = create_engine(app_config.source_db)
        if not test_connection(engine):
            pytest.skip("MySQL connection not available.")

        count = get_row_count(engine, "employees")
        assert count > 0, "employees table is empty"


class TestWorkloadFile:
    """Workload file tests."""

    def test_workload_file_exists(self):
        """The workload SQL file exists."""
        workload = Path("workloads/workload.sql")
        assert workload.exists(), f"Workload file not found at {workload.resolve()}"

    def test_workload_has_queries(self):
        """The workload file contains parseable SQL queries."""
        from safeslicesql.workload_analyzer import load_workload

        workload = Path("workloads/workload.sql")
        queries = load_workload(workload)
        assert len(queries) >= 5, f"Expected at least 5 queries, found {len(queries)}"

    @pytest.mark.db
    def test_workload_queries_execute(self, app_config):
        """All workload queries execute successfully on the source database."""
        from sqlalchemy import text

        from safeslicesql.db import create_engine, test_connection
        from safeslicesql.workload_analyzer import load_workload

        engine = create_engine(app_config.source_db)
        if not test_connection(engine):
            pytest.skip("MySQL connection not available.")

        queries = load_workload("workloads/workload.sql")
        errors: list[str] = []
        with engine.connect() as conn:
            for i, query in enumerate(queries, 1):
                try:
                    result = conn.execute(text(query))
                    rows = result.fetchall()
                    print(f"  Q{i}: {len(rows)} rows returned")
                except Exception as exc:
                    errors.append(f"Q{i} failed: {exc}")

        assert not errors, "Some workload queries failed:\n" + "\n".join(errors)


class TestCLI:
    """CLI entry point tests."""

    def test_cli_help(self):
        """CLI entry point is installed and responds to --help."""
        result = subprocess.run(
            [*_get_safeslice_cmd(), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_get_env_with_pythonpath(),
            timeout=30,
        )
        assert result.returncode == 0, (
            f"safeslice --help failed with exit code {result.returncode}. stderr: {result.stderr}"
        )
        assert "safeslice" in result.stdout.lower() or "SafeSliceSQL" in result.stdout, (
            f"CLI help output does not mention safeslice: {result.stdout}"
        )

    def test_cli_inspect_help(self):
        """inspect subcommand is registered."""
        result = subprocess.run(
            [*_get_safeslice_cmd(), "inspect", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_get_env_with_pythonpath(),
            timeout=30,
        )
        assert result.returncode == 0
        assert "database" in result.stdout.lower()

    def test_cli_run_help(self):
        """run subcommand is registered."""
        result = subprocess.run(
            [*_get_safeslice_cmd(), "run", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_get_env_with_pythonpath(),
            timeout=30,
        )
        assert result.returncode == 0
        assert "method" in result.stdout.lower()

    def test_cli_validate_help(self):
        """validate subcommand is registered."""
        result = subprocess.run(
            [*_get_safeslice_cmd(), "validate", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_get_env_with_pythonpath(),
            timeout=30,
        )
        assert result.returncode == 0

    def test_cli_benchmark_help(self):
        """benchmark subcommand is registered."""
        result = subprocess.run(
            [*_get_safeslice_cmd(), "benchmark", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_get_env_with_pythonpath(),
            timeout=30,
        )
        assert result.returncode == 0


class TestModuleImports:
    """Verify all module stubs are importable."""

    def test_import_config(self):
        import safeslicesql.config  # noqa: F401

    def test_import_db(self):
        import safeslicesql.db  # noqa: F401

    def test_import_types(self):
        import safeslicesql.types  # noqa: F401

    def test_import_schema_analyzer(self):
        import safeslicesql.schema_analyzer  # noqa: F401

    def test_import_dependency_graph(self):
        import safeslicesql.dependency_graph  # noqa: F401

    def test_import_workload_analyzer(self):
        import safeslicesql.workload_analyzer  # noqa: F401

    def test_import_sampler(self):
        import safeslicesql.sampler  # noqa: F401

    def test_import_subset_generator(self):
        import safeslicesql.subset_generator  # noqa: F401

    def test_import_validator(self):
        import safeslicesql.validator  # noqa: F401

    def test_import_benchmark(self):
        import safeslicesql.benchmark  # noqa: F401

    def test_import_reporting(self):
        import safeslicesql.reporting  # noqa: F401

    def test_import_cli(self):
        import safeslicesql.cli  # noqa: F401

    def test_import_logging_config(self):
        import safeslicesql.logging_config  # noqa: F401
