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


def _clean_ansi(text: str) -> str:
    """Strip ANSI escape sequences."""
    import re

    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


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
    """Connectivity checks for the configured source, including known samples."""

    def test_source_connection(self, live_source_engine):
        with live_source_engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1

    def test_configured_source_tables_exist(self, live_source_engine):
        import sqlalchemy as sa

        tables = set(sa.inspect(live_source_engine).get_table_names())
        assert tables, "Configured source database has no tables."
        known = {
            "world": {"country", "city", "countrylanguage"},
            "employees": {
                "employees",
                "departments",
                "dept_emp",
                "dept_manager",
                "titles",
                "salaries",
            },
        }
        expected = known.get(live_source_engine.url.database, set())
        assert expected <= tables, f"Missing expected sample tables: {expected - tables}"

    def test_configured_source_has_rows(self, live_source_engine):
        import sqlalchemy as sa

        from safeslicesql.db import get_row_count

        tables = sa.inspect(live_source_engine).get_table_names()
        assert sum(get_row_count(live_source_engine, name) for name in tables) > 0, (
            "Configured source has no data."
        )


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
    def test_workload_queries_execute(self, live_source_engine, tmp_path):
        """Use source-specific SQL, or basic probes for an unfamiliar schema."""
        import sqlalchemy as sa

        from safeslicesql.db import quote_ident
        from safeslicesql.workload_analyzer import analyze_workload

        database = live_source_engine.url.database
        workload = Path("workloads") / f"{database}_workload.sql"
        if not workload.exists():
            if database == "employees":
                workload = Path("workloads/workload.sql")
            else:
                workload = tmp_path / "source_probes.sql"
                tables = sa.inspect(live_source_engine).get_table_names()
                workload.write_text(
                    ";\n".join(f"SELECT COUNT(*) FROM {quote_ident(name)}" for name in tables),
                    encoding="utf-8",
                )
        analysis = analyze_workload(workload, live_source_engine)
        assert analysis.supported_queries, "No supported queries to test."
        errors = [
            query.analysis_error for query in analysis.supported_queries if query.analysis_error
        ]
        assert not errors, "Workload failed on configured source: " + "; ".join(errors)


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
        clean_out = _clean_ansi(result.stdout)
        assert "safeslice" in clean_out.lower() or "SafeSliceSQL" in clean_out, (
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
        assert "database" in _clean_ansi(result.stdout).lower()

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
        assert "method" in _clean_ansi(result.stdout).lower()

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
