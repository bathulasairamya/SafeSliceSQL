"""Regression coverage for audit issues 26-36."""

import logging
import random
import tracemalloc
from functools import partial
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
import yaml
from typer.testing import CliRunner

import safeslicesql.benchmark as benchmark
import safeslicesql.cli as cli
import safeslicesql.config as config
import safeslicesql.pipeline as pipeline
import safeslicesql.result_store as result_store
from safeslicesql.sampler import _sample_primary_keys, select_rows
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.types import BenchmarkResults
from safeslicesql.validator import evaluate_workload
from safeslicesql.workload_analyzer import WorkloadAnalysis, analyze_workload


@pytest.fixture(autouse=True)
def environment(tmp_path, monkeypatch):
    import os

    for key in os.environ:
        if key.startswith("SAFESLICE_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("SAFESLICE_SOURCE_USER", "reader")
    monkeypatch.setenv("SAFESLICE_LOG_DIR", str(tmp_path / "logs"))
    yield
    logger = logging.getLogger("safeslicesql")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()


@pytest.fixture
def database():
    engines = []

    def create(*statements):
        engine = sa.create_engine("sqlite:///:memory:")
        engines.append(engine)
        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
        return engine

    yield create
    for engine in engines:
        engine.dispose()


@pytest.mark.parametrize("failed,total,exit_code", [(1, 1, 1), (1, 2, 1), (0, 1, 0), (0, 0, 1)])
def test_benchmark_cli_propagates_failures(monkeypatch, failed, total, exit_code):
    monkeypatch.setattr(
        benchmark, "run_benchmark", lambda _: BenchmarkResults(total_runs=total, failed_runs=failed)
    )
    result = CliRunner().invoke(cli.app, ["benchmark"])
    assert result.exit_code == exit_code, result.output


@pytest.mark.parametrize("composite", [False, True])
def test_seeded_reservoir_ignores_unordered_scan_direction(database, composite):
    engine = database(
        "CREATE TABLE t(a INTEGER,b INTEGER,PRIMARY KEY(a,b))"
        if composite
        else "CREATE TABLE t(a INTEGER PRIMARY KEY,b INTEGER)"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO t VALUES (?,?)", [(i, 100 - i) for i in range(100)])
    schema = inspect_schema(engine)
    first = _sample_primary_keys(schema, "t", engine, 10, random.Random(42))
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA reverse_unordered_selects=ON")
    second = _sample_primary_keys(schema, "t", engine, 10, random.Random(42))
    assert len(first) == 10
    assert first == second


@pytest.mark.parametrize(
    "matrix",
    [
        {"budget_percents": [-10]},
        {"budget_percents": [200]},
        {"budget_percents": [0]},
        {"budget_percents": [True]},
        {"budget_percents": [float("nan")]},
        {"budget_percents": [float("inf")]},
        {"budget_percents": ["20"]},
        {"budget_percents": []},
        {"methods": []},
        {"methods": "referential"},
        {"methods": ["unknown"]},
        {"seeds": []},
        {"seeds": [False]},
        {"seeds": [1.5]},
    ],
)
def test_invalid_benchmark_matrix_is_rejected(tmp_path, matrix):
    path = tmp_path / "benchmark.yaml"
    path.write_text(yaml.safe_dump({"matrix": matrix}), encoding="utf-8")
    with pytest.raises(ValueError, match="matrix"):
        benchmark.load_benchmark_config(path)


@pytest.mark.parametrize(
    "data", [[], False, {"matrix": None}, {"output": []}, {"workload": {"path": None}}]
)
def test_malformed_benchmark_sections_are_actionable(tmp_path, data):
    path = tmp_path / "benchmark.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError):
        benchmark.load_benchmark_config(path)


def test_fractional_valid_benchmark_budget(tmp_path):
    path = tmp_path / "benchmark.yaml"
    path.write_text("matrix:\n  budget_percents: [0.5, 100]\n", encoding="utf-8")
    assert benchmark.load_benchmark_config(path)["budget_percents"] == [0.5, 100]


@pytest.mark.parametrize("field", ["get_columns", "get_pk_constraint", "get_foreign_keys"])
def test_introspection_errors_never_return_partial_schema(database, monkeypatch, field):
    engine = database("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    inspector = sa.inspect(engine)
    monkeypatch.setattr(inspector, field, Mock(side_effect=RuntimeError("permission denied")))
    monkeypatch.setattr(sa, "inspect", lambda _: inspector)
    with pytest.raises(RuntimeError, match="table 't'"):
        inspect_schema(engine)


def test_failed_row_count_does_not_become_zero(database):
    engine = database("CREATE TABLE t(id INTEGER PRIMARY KEY)")

    @sa.event.listens_for(engine, "before_cursor_execute")
    def fail_count(conn, cursor, statement, parameters, context, executemany):
        if "COUNT(*)" in statement:
            raise RuntimeError("count denied")

    with pytest.raises(RuntimeError, match="count rows in table 't'"):
        inspect_schema(engine)


def test_inspect_only_requires_source_and_applies_override(database, monkeypatch):
    engine = database("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("SAFESLICE_TARGET_PORT", "invalid-but-unused")
    factory = Mock(return_value=engine)
    monkeypatch.setattr(cli, "create_engine", factory)
    result = CliRunner().invoke(cli.app, ["inspect", "--database", "source_override"])
    assert result.exit_code == 0, result.output
    assert factory.call_args.args[0].database == "source_override"


def test_dry_run_never_constructs_or_connects_target(database, tmp_path, monkeypatch):
    engine = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    monkeypatch.setenv("SAFESLICE_TARGET_PORT", "invalid-but-unused")
    monkeypatch.setattr(cli, "get_source_engine", lambda _: engine)
    target_factory = Mock(side_effect=AssertionError("dry run touched target"))
    monkeypatch.setattr(cli, "get_target_engine", target_factory)
    path = tmp_path / "workload.sql"
    path.write_text("SELECT * FROM t", encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--method",
            "workload-aware",
            "--budget-percent",
            "100",
            "--source",
            "source_override",
            "--dry-run",
            "--workload",
            str(path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Dry-run requested" in result.output
    target_factory.assert_not_called()


@pytest.mark.parametrize("original_name", ["", "same_database"])
def test_run_overrides_repair_missing_or_conflicting_database_names(
    database, tmp_path, monkeypatch, original_name
):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    target = database()
    monkeypatch.setenv("SAFESLICE_SOURCE_DATABASE", original_name)
    monkeypatch.setenv("SAFESLICE_TARGET_DATABASE", original_name)
    monkeypatch.setenv("SAFESLICE_TARGET_USER", "writer")
    source_factory, target_factory = Mock(return_value=source), Mock(return_value=target)
    monkeypatch.setattr(cli, "get_source_engine", source_factory)
    monkeypatch.setattr(cli, "get_target_engine", target_factory)
    path = tmp_path / "workload.sql"
    path.write_text("SELECT * FROM t", encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--method",
            "referential",
            "--budget-percent",
            "100",
            "--source",
            "new_source",
            "--target",
            "new_target",
            "--workload",
            str(path),
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert source_factory.call_args.args[0].source_db.database == "new_source"
    assert target_factory.call_args.args[0].target_db.database == "new_target"


@pytest.mark.parametrize("sql", [None, "", "SELECT 1", "SELECT 'unterminated FROM t"])
def test_unusable_workload_stops_cli_before_selection(database, tmp_path, monkeypatch, sql):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    monkeypatch.setattr(cli, "get_source_engine", lambda _: source)
    select = Mock(side_effect=AssertionError("selection should not run"))
    monkeypatch.setattr(cli, "select_rows", select)
    path = tmp_path / "workload.sql"
    if sql is not None:
        path.write_text(sql, encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--method",
            "referential",
            "--budget-percent",
            "100",
            "--source",
            "src",
            "--dry-run",
            "--workload",
            str(path),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "Workload analysis failed" in result.output
    select.assert_not_called()


def test_missing_benchmark_workload_stops_before_selection(database, tmp_path, monkeypatch):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    target = database()
    path = tmp_path / "benchmark.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "workload": {"path": str(tmp_path / "missing.sql")},
                "output": {
                    key: str(tmp_path / key) for key in ("raw_dir", "summary_dir", "plots_dir")
                },
            }
        ),
        encoding="utf-8",
    )
    select = Mock(side_effect=AssertionError("selection should not run"))
    monkeypatch.setattr(pipeline, "select_rows", select)
    with pytest.raises(FileNotFoundError, match="Workload file"):
        benchmark.run_benchmark(path, source, target)
    select.assert_not_called()


@pytest.mark.parametrize("workload", [None, WorkloadAnalysis()])
def test_workload_aware_api_rejects_missing_queries(database, workload):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="Workload"):
        select_rows(
            inspect_schema(source), workload, engine=source, method="workload-aware", budget=1
        )


def test_cli_environment_logging_and_debug_file_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFESLICE_LOG_LEVEL", "ERROR")
    result = CliRunner().invoke(cli.app, ["run", "--method", "bad", "--budget-percent", "20"])
    assert result.exit_code == 2
    project_logger = logging.getLogger("safeslicesql")
    console = next(h for h in project_logger.handlers if not isinstance(h, logging.FileHandler))
    assert console.level == logging.ERROR
    project_logger.debug("debug-file-regression")
    assert "debug-file-regression" in (tmp_path / "logs/safeslicesql.log").read_text(
        encoding="utf-8"
    )
    result = CliRunner().invoke(
        cli.app, ["run", "--method", "bad", "--budget-percent", "20", "--log-level", "WARNING"]
    )
    assert result.exit_code == 2
    console = next(h for h in project_logger.handlers if not isinstance(h, logging.FileHandler))
    assert console.level == logging.WARNING


def test_large_results_stream_with_bounded_memory(database, tmp_path):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT)")
    with source.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO t VALUES (?,?)", [(i, "x" * 1024) for i in range(20000)]
        )
    path = tmp_path / "workload.sql"
    path.write_text("SELECT * FROM t", encoding="utf-8")
    tracemalloc.start()
    try:
        analysis = analyze_workload(path, source)
        report = evaluate_workload(source, source, analysis)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert analysis.queries[0].source_row_count == 20000
    assert report.overall_result_retention == 1.0
    assert report.failed_queries == 0
    assert peak < 8 * 1024 * 1024, f"Peak Python allocations: {peak}"


@pytest.mark.parametrize("target_fails", [False, True])
def test_temporary_result_data_is_removed_on_success_and_failure(
    database, tmp_path, monkeypatch, target_fails
):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    target = database() if target_fails else source
    path = tmp_path / "workload.sql"
    path.write_text("SELECT * FROM t", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(
        result_store, "TemporaryDirectory", partial(result_store.TemporaryDirectory, dir=scratch)
    )
    report = evaluate_workload(source, target, analyze_workload(path, None))
    assert report.failed_queries == int(target_fails)
    assert list(scratch.iterdir()) == []
