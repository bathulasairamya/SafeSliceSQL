"""
tests/test_benchmark.py — Phase 8 unit & integration tests.

Verifies:
- Benchmark configuration loading & validation
- End-to-end benchmark runner across methods, budgets, and seeds
- Aggregated CSV export and Markdown summary generation
- Publication-quality plot generation
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from safeslicesql.benchmark import load_benchmark_config, run_benchmark
from scripts.generate_plots import generate_all_plots


@pytest.fixture
def test_db_pair(tmp_path):
    """Create a pair of SQLite databases (source and target) with relational test data."""
    src_db = tmp_path / "source.db"
    tgt_db = tmp_path / "target.db"

    src_engine = sa.create_engine(f"sqlite:///{src_db}")
    tgt_engine = sa.create_engine(f"sqlite:///{tgt_db}")

    with src_engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON;"))
        conn.execute(
            text("""
            CREATE TABLE departments (
                dept_no VARCHAR(4) PRIMARY KEY,
                dept_name VARCHAR(40) NOT NULL
            );
        """)
        )
        conn.execute(
            text("""
            CREATE TABLE employees (
                emp_no INTEGER PRIMARY KEY,
                first_name VARCHAR(20) NOT NULL,
                last_name VARCHAR(20) NOT NULL
            );
        """)
        )
        conn.execute(
            text("""
            CREATE TABLE dept_emp (
                id INTEGER PRIMARY KEY,
                emp_no INTEGER NOT NULL,
                dept_no VARCHAR(4) NOT NULL,
                FOREIGN KEY (emp_no) REFERENCES employees(emp_no),
                FOREIGN KEY (dept_no) REFERENCES departments(dept_no)
            );
        """)
        )

        # Seed data
        conn.execute(
            text("INSERT INTO departments VALUES ('d001', 'Engineering'), ('d002', 'Sales');")
        )
        conn.execute(
            text("""
            INSERT INTO employees VALUES
                (101, 'Ada', 'Lovelace'),
                (102, 'Alan', 'Turing'),
                (103, 'Grace', 'Hopper');
        """)
        )
        conn.execute(
            text("""
            INSERT INTO dept_emp VALUES
                (1, 101, 'd001'),
                (2, 102, 'd001'),
                (3, 103, 'd002');
        """)
        )
        conn.commit()

    return src_engine, tgt_engine


def test_load_benchmark_config(tmp_path):
    """Test loading benchmark config with custom values."""
    yaml_content = """
matrix:
  methods: ["random-repair", "referential"]
  budget_percents: [20, 50]
  seeds: [42, 99]
workload:
  path: "workloads/workload.sql"
output:
  raw_dir: "results/raw_test"
  summary_dir: "results/summary_test"
  plots_dir: "results/plots_test"
"""
    cfg_file = tmp_path / "bench.yaml"
    cfg_file.write_text(yaml_content, encoding="utf-8")

    cfg = load_benchmark_config(cfg_file)
    assert cfg["methods"] == ["random-repair", "referential"]
    assert cfg["budget_percents"] == [20, 50]
    assert cfg["seeds"] == [42, 99]
    assert cfg["raw_dir"] == "results/raw_test"


def test_load_benchmark_config_missing():
    """Verify error on non-existent config file."""
    with pytest.raises(FileNotFoundError):
        load_benchmark_config("nonexistent_path_to_config.yaml")


def test_run_benchmark_end_to_end(tmp_path, test_db_pair):
    """Test full execution of benchmark matrix with SQLite engines."""
    src_engine, tgt_engine = test_db_pair

    # Create a small workload file
    wl_file = tmp_path / "test_workload.sql"
    wl_file.write_text(
        """
-- Q1: List all employees
SELECT * FROM employees;

-- Q2: Count by department
SELECT dept_no, COUNT(*) as cnt FROM dept_emp GROUP BY dept_no;
""",
        encoding="utf-8",
    )

    bench_yaml = tmp_path / "matrix.yaml"
    raw_dir = tmp_path / "raw"
    summary_dir = tmp_path / "summaries"
    plots_dir = tmp_path / "plots"

    bench_yaml.write_text(
        f"""
matrix:
  methods: ["referential", "workload-aware"]
  budget_percents: [50]
  seeds: [42]
workload:
  path: "{wl_file.as_posix()}"
output:
  raw_dir: "{raw_dir.as_posix()}"
  summary_dir: "{summary_dir.as_posix()}"
  plots_dir: "{plots_dir.as_posix()}"
""",
        encoding="utf-8",
    )

    results = run_benchmark(
        config_path=bench_yaml,
        source_engine=src_engine,
        target_engine=tgt_engine,
        target_database_name="target",
    )

    assert results.total_runs == 2
    assert results.successful_runs == 2
    assert results.failed_runs == 0

    # Verify summary files were created
    assert (summary_dir / "benchmark_summary.csv").exists()
    assert (summary_dir / "benchmark_summary.md").exists()
    assert (raw_dir / "all_benchmark_runs.csv").exists()

    # Verify plot generation
    plots = list(plots_dir.glob("*.png"))
    assert len(plots) > 0


def test_generate_plots_standalone(tmp_path):
    """Test plot generation from standalone summary CSV."""
    csv_file = tmp_path / "sample_summary.csv"
    csv_file.write_text(
        """method,budget_percent,query_survival_rate_mean,query_survival_rate_std,overall_result_retention_mean,overall_result_retention_std,overall_key_retention_mean,overall_key_retention_std,overall_aggregate_retention_mean,overall_aggregate_retention_std,closure_additions_mean,closure_additions_std,total_selected_mean,total_selected_std,runtime_selection_mean,runtime_selection_std,total_runtime_mean,total_runtime_std
workload-aware,20,0.85,0.02,0.80,0.03,0.90,0.01,0.88,0.02,12,2,100,5,0.15,0.01,0.5,0.05
referential,20,0.60,0.05,0.55,0.04,0.70,0.02,0.65,0.03,8,1,100,2,0.10,0.01,0.4,0.03
workload-aware,40,0.95,0.01,0.92,0.02,0.98,0.01,0.96,0.01,20,3,200,6,0.25,0.02,0.8,0.06
referential,40,0.75,0.04,0.72,0.03,0.85,0.02,0.80,0.02,15,2,200,4,0.18,0.01,0.7,0.04
""",
        encoding="utf-8",
    )

    plots_out = tmp_path / "output_plots"
    plots = generate_all_plots(csv_file, plots_out)
    assert len(plots) >= 4
    for p in plots:
        assert p.exists()
        assert p.stat().st_size > 1000  # Non-empty image file
