"""Desktop workflow and result states reuse the core pipeline without live user data."""

import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy as sa

import safeslicesql.desktop_service as service
from safeslicesql.acceptance import AcceptanceReport
from safeslicesql.automatic_workload import save_automatic_workload
from safeslicesql.desktop_service import CopyRequest
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.types import (
    BenchmarkResults,
    BenchmarkRunResult,
    SelectionResult,
    ValidationReport,
    WorkloadReport,
)


@pytest.mark.parametrize(
    "job",
    [
        CopyRequest("csv", ""),
        CopyRequest("other", "data"),
        CopyRequest("csv", "data", budget=0),
        CopyRequest("csv", "data", budget=101),
        CopyRequest("csv", "data", method="unknown"),
    ],
)
def test_invalid_request_never_prepares_data(tmp_path, monkeypatch, job):
    prepare = Mock()
    monkeypatch.setattr(service, "prepare_dataset", prepare)
    with pytest.raises(ValueError):
        service.create_copy(Mock(), job, tmp_path / "output")
    prepare.assert_not_called()


def test_desktop_request_runs_real_pipeline_and_comparison(tmp_path, monkeypatch):
    source_url = sa.URL.create("sqlite", database=str(tmp_path / "source.db"))
    target_url = sa.URL.create("sqlite", database=str(tmp_path / "target.db"))
    source = sa.create_engine(source_url)
    try:
        with source.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE supplied(id INTEGER PRIMARY KEY,category TEXT)"
            )
            connection.exec_driver_sql("INSERT INTO supplied VALUES(1,'A'),(2,'B')")
        workload = save_automatic_workload(inspect_schema(source), tmp_path / "queries")
    finally:
        source.dispose()
    prepared = SimpleNamespace(
        source=SimpleNamespace(url=source_url, database="supplied"),
        target=SimpleNamespace(url=target_url, database="separate_copy"),
        workload=workload,
        tables={"supplied": 2},
    )
    prepare = Mock(return_value=prepared)
    monkeypatch.setattr(service, "prepare_dataset", prepare)
    progress = []
    request = CopyRequest("csv", "faculty.csv", budget=100, compare=True)
    outcome = service.create_copy(Mock(), request, tmp_path / "output", progress.append)
    assert outcome.results.successful_runs == 3
    assert all(run.workload.overall_result_retention == 1 for run in outcome.results.runs)
    assert len(progress) == 2
    assert prepare.call_args.kwargs["files"] == Path("faculty.csv")
    assert outcome.request is request
    assert len(list((outcome.folder / "plots").glob("*.png"))) == 4


def screen():
    pytest.importorskip("tkinter")
    from safeslicesql.desktop import Desktop

    ui = Desktop.__new__(Desktop)
    for name in (
        "summary",
        "status",
        "details",
        "report_button",
        "chart_button",
        "verify_button",
        "export_button",
        "results",
        "progress",
        "root",
    ):
        setattr(ui, name, Mock())
    ui.events = queue.Queue()
    ui._lock = Mock()
    ui.busy = True
    return ui


def outcome(tmp_path, success=True):
    run = BenchmarkRunResult(
        "run",
        "workload-aware",
        42,
        10,
        100,
        selection=SelectionResult("workload-aware", 42, 10, {}),
        validation=ValidationReport(actual_rows=10, budget_limit=10, source_unchanged=True),
        workload=WorkloadReport(survival_denominator=2),
        success=success,
        acceptance=AcceptanceReport(success),
        error_message="" if success else "injected failure",
    )
    return SimpleNamespace(
        request=CopyRequest("csv", "faculty.csv"),
        prepared=SimpleNamespace(
            source=SimpleNamespace(database="source"),
            target=SimpleNamespace(database="target"),
            tables={"supplied": 100},
        ),
        folder=tmp_path,
        results=BenchmarkResults(
            runs=[run], total_runs=1, successful_runs=int(success), failed_runs=int(not success)
        ),
    )


def test_completed_copy_shows_plain_language_and_input_name(tmp_path):
    ui = screen()
    ui._show_outcome(outcome(tmp_path))
    assert "Copy ready for faculty.csv" in ui.summary.set.call_args.args[0]
    assert ui.report_button.configure.call_args.kwargs["state"] == "normal"
    assert "target" in ui.details.set.call_args.args[0]
    assert ui.results.insert.call_count == 6


def test_failed_run_never_claims_copy_ready(tmp_path):
    ui = screen()
    ui._show_outcome(outcome(tmp_path, success=False))
    assert "needs attention" in ui.summary.set.call_args.args[0]
    assert "injected failure" in ui.details.set.call_args.args[0]


def test_worker_failure_unlocks_controls_and_keeps_details():
    ui = screen()
    ui.events.put(("error", "Database connection unavailable"))
    from unittest.mock import patch

    with patch("safeslicesql.desktop.show_error"):
        ui._poll()
    assert ui.busy is False
    ui._lock.assert_called_once_with(False)
    assert ui.details.set.call_args.args[0] == "Database connection unavailable"
    ui.progress.stop.assert_called_once()


def test_close_during_work_preserves_running_job(monkeypatch):
    ui = screen()
    from safeslicesql import desktop

    monkeypatch.setattr(desktop.messagebox, "showinfo", Mock())
    ui._close()
    ui.root.destroy.assert_not_called()
