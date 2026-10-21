"""
Tests for safeslicesql.reporting — Phase 7.
"""

from __future__ import annotations

import csv
from pathlib import Path

from safeslicesql.reporting import (
    print_generation_summary,
    print_selection_summary,
    print_validation_summary,
    print_workload_summary,
    save_run_results_csv,
)
from safeslicesql.types import (
    BenchmarkRunResult,
    GenerationResult,
    QueryResult,
    SelectionResult,
    ValidationReport,
    WorkloadReport,
)


def test_save_run_results_csv(tmp_path: Path):
    """Verify that save_run_results_csv writes summary and query breakdown CSVs."""
    selection = SelectionResult(
        method="workload-aware",
        seed=42,
        budget=100,
        selected_rows={"departments": ["d1"], "employees": [1, 2]},
        total_selected=3,
        closure_additions=1,
        rejected_candidates=0,
    )
    generation = GenerationResult(
        target_database="test_target",
        tables_created=["departments", "employees"],
        rows_inserted={"departments": 1, "employees": 2},
        total_rows_inserted=3,
        insertion_order=["departments", "employees"],
        success=True,
    )
    validation = ValidationReport(
        pk_unique=True,
        fk_violations=0,
        budget_compliant=True,
        actual_rows=3,
        budget_limit=100,
        tables_present=["departments", "employees"],
        source_unchanged=True,
        schema_compatible=True,
    )
    workload = WorkloadReport(
        per_query_results=[
            QueryResult(
                query_id="Q1",
                query_text="SELECT * FROM employees WHERE emp_no = 1",
                source_row_count=1,
                target_row_count=1,
                survived=True,
                key_retention=1.0,
                result_row_retention=1.0,
                aggregate_retention=1.0,
            ),
            QueryResult(
                query_id="Q2",
                query_text="SELECT * FROM complex_view",
                excluded=True,
                exclusion_reason="Unsupported syntax",
            ),
        ],
        total_queries=2,
        supported_queries=1,
        excluded_queries=1,
        source_nonempty_queries=1,
        query_survival_rate=1.0,
        overall_key_retention=1.0,
        overall_result_retention=1.0,
        overall_aggregate_retention=1.0,
    )

    run_result = BenchmarkRunResult(
        run_id="test_run_01",
        method="workload-aware",
        seed=42,
        budget_percent=20.0,
        dataset_size=15,
        selection=selection,
        generation=generation,
        validation=validation,
        workload=workload,
        runtime_selection=0.1,
        runtime_materialization=0.2,
        runtime_validation=0.05,
        runtime_workload_eval=0.15,
        success=True,
    )

    csv_path = save_run_results_csv(run_result, output_dir=tmp_path)
    assert csv_path.exists()
    assert csv_path.name == "run_test_run_01.csv"

    # Verify CSV content
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        row = reader[0]
        assert row["run_id"] == "test_run_01"
        assert row["method"] == "workload-aware"
        assert row["seed"] == "42"
        assert row["success"] == "True"
        assert row["total_selected"] == "3"
        assert row["total_rows_inserted"] == "3"
        assert row["is_valid"] == "True"

    # Verify queries breakdown CSV
    queries_path = tmp_path / "queries_test_run_01.csv"
    assert queries_path.exists()
    with open(queries_path, mode="r", encoding="utf-8") as f:
        q_reader = list(csv.DictReader(f))
        assert len(q_reader) == 2
        assert q_reader[0]["query_id"] == "Q1"
        assert q_reader[0]["survived"] == "True"
        assert q_reader[1]["query_id"] == "Q2"
        assert q_reader[1]["excluded"] == "True"


def test_console_summary_printers():
    """Verify that all Rich console printers run without error."""
    val = ValidationReport(
        pk_unique=True,
        fk_violations=1,
        fk_violation_details=["1 orphaned row in employees"],
        budget_compliant=False,
        actual_rows=15,
        budget_limit=10,
        tables_present=["departments"],
        tables_missing=["salaries"],
        source_unchanged=True,
        schema_compatible=False,
        errors=["Missing tables"],
    )
    # Should not raise exception
    print_validation_summary(val)

    wl = WorkloadReport(
        per_query_results=[
            QueryResult(query_id="Q1", query_text="SELECT 1", survived=True),
            QueryResult(query_id="Q2", query_text="SELECT 2", error="Syntax error"),
            QueryResult(
                query_id="Q3", query_text="SELECT 3", excluded=True, exclusion_reason="Window func"
            ),
        ],
        total_queries=3,
        supported_queries=2,
        excluded_queries=1,
        query_survival_rate=0.5,
    )
    print_workload_summary(wl)

    sel = SelectionResult(
        method="referential",
        seed=1,
        budget=50,
        selected_rows={"departments": ["d1"], "employees": [1]},
        total_selected=2,
    )
    print_selection_summary(sel)

    gen = GenerationResult(
        target_database="test_db",
        tables_created=["departments", "employees"],
        rows_inserted={"departments": 1, "employees": 1},
        total_rows_inserted=2,
        insertion_order=["departments", "employees"],
        success=False,
        error_message="Something went wrong",
    )
    print_generation_summary(gen)
