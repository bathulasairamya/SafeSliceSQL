"""Regression coverage for audit issues 16-25."""

import pandas as pd
import pytest
import sqlalchemy as sa

from safeslicesql.benchmark import _compile_and_save_summaries, run_benchmark
from safeslicesql.source_snapshot import capture_source_snapshot
from safeslicesql.types import BenchmarkRunResult, WorkloadReport
from safeslicesql.validator import evaluate_workload, validate_subset
from safeslicesql.workload_analyzer import analyze_workload


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


def workload(tmp_path, sql, source=None):
    path = tmp_path / "queries.sql"
    path.write_text(sql, encoding="utf-8")
    return analyze_workload(path, source)


def test_unaliased_join_and_unqualified_group_features(tmp_path):
    analysis = workload(
        tmp_path,
        "SELECT a.id,b.id FROM a JOIN b ON a.id=b.id;"
        "SELECT department,COUNT(*) AS n FROM employees WHERE salary>10 AND active=1 GROUP BY department ORDER BY n LIMIT 1;",
    )
    assert analysis.queries[0].tables == ["a", "b"]
    assert analysis.queries[0].join_tables == [("a", "b")]
    assert analysis.queries[1].group_columns == [("employees", "department")]
    assert analysis.queries[1].filter_columns == [("employees", "salary"), ("employees", "active")]


def test_quoted_alias_features_ignore_literal_contents(tmp_path):
    analysis = workload(
        tmp_path,
        "SELECT `e`.`team` AS label,COUNT(*) FROM `employees` AS `e` "
        "JOIN `teams` AS `t` ON `e`.`team`=`t`.`id` "
        "WHERE `e`.`name`='fake.column GROUP BY bogus' GROUP BY `e`.`team`;",
    )
    query = analysis.queries[0]
    assert query.tables == ["employees", "teams"]
    assert query.join_tables == [("employees", "teams")]
    assert query.filter_columns == [("employees", "name")]
    assert query.group_columns == [("employees", "team")]
    assert query.group_output_positions == [0]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t WHERE id IN (SELECT id FROM t)",
        "SELECT (SELECT MAX(id) FROM t) FROM t",
        "SELECT * FROM (SELECT * FROM t) q",
        "WITH q AS (SELECT * FROM t) SELECT * FROM q",
        "SELECT id FROM t UNION SELECT id FROM t",
        "SELECT id FROM t INTERSECT SELECT id FROM t",
        "SELECT id FROM t EXCEPT SELECT id FROM t",
        "SELECT SUM(id) OVER () FROM t",
    ],
)
def test_unsupported_patterns_excluded_before_execution(database, tmp_path, sql):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    executed = []

    @sa.event.listens_for(source, "before_cursor_execute")
    def record(_conn, _cursor, statement, _params, _context, _many):
        executed.append(statement)

    analysis = workload(tmp_path, sql, source)
    assert not analysis.queries[0].is_supported
    assert sql not in executed
    assert analysis.queries[0].exclusion_reason


def test_keywords_in_literals_do_not_exclude_valid_query(database, tmp_path):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    analysis = workload(tmp_path, "SELECT 'DROP SELECT UNION OVER' AS message FROM t", source)
    assert analysis.queries[0].is_supported
    assert analysis.queries[0].source_row_count == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE t SET value='changed' WHERE id=1",
        "DELETE FROM t WHERE id=1; INSERT INTO t VALUES(9,'replacement')",
        "ALTER TABLE t ADD COLUMN extra INTEGER",
    ],
)
def test_content_snapshot_detects_same_count_changes(database, mutation):
    ddl = "CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT)"
    source = database(ddl, "INSERT INTO t VALUES(1,'before')")
    target = database(ddl, "INSERT INTO t VALUES(1,'before')")
    baseline = capture_source_snapshot(source)
    with source.begin() as connection:
        for statement in mutation.split(";"):
            connection.exec_driver_sql(statement)
    report = validate_subset(source, target, 10, initial_source_snapshot=baseline)
    assert report.source_unchanged is False
    assert not report.is_valid
    assert any("baseline" in error for error in report.errors)


def test_without_content_baseline_source_is_not_claimed_unchanged(database):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    target = database("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    assert validate_subset(source, target, 10).source_unchanged is None
    assert validate_subset(source, target, 10, initial_source_row_count=0).source_unchanged is None
    assert validate_subset(source, target, 10, initial_source_row_count=1).source_unchanged is False
    assert (
        validate_subset(
            source, target, 10, initial_source_snapshot=capture_source_snapshot(source)
        ).source_unchanged
        is True
    )


def test_snapshot_is_order_independent_and_counts_duplicate_rows(database):
    source = database("CREATE TABLE t(value TEXT)", "INSERT INTO t VALUES('x'),('x'),('y')")
    baseline = capture_source_snapshot(source)
    with source.begin() as connection:
        connection.exec_driver_sql("PRAGMA reverse_unordered_selects=ON")
    assert capture_source_snapshot(source) == baseline
    with source.begin() as connection:
        connection.exec_driver_sql("DELETE FROM t WHERE rowid=1")
        connection.exec_driver_sql("INSERT INTO t VALUES('y')")
    assert capture_source_snapshot(source) != baseline


@pytest.mark.parametrize("target_rows,expected", [("(1,'x'),(2,'x')", 1.0), ("(1,'x')", 0.5)])
def test_duplicate_result_multiplicity_is_preserved(database, tmp_path, target_rows, expected):
    ddl = "CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT)"
    source = database(ddl, "INSERT INTO t VALUES(1,'x'),(2,'x')")
    target = database(ddl, f"INSERT INTO t VALUES{target_rows}")
    report = evaluate_workload(source, target, workload(tmp_path, "SELECT value FROM t"))
    assert report.overall_result_retention == expected


def test_duplicate_output_names_preserve_both_values(database, tmp_path):
    ddl = ["CREATE TABLE a(id INTEGER PRIMARY KEY)", "CREATE TABLE b(id INTEGER PRIMARY KEY)"]
    source = database(*ddl, "INSERT INTO a VALUES(1)", "INSERT INTO b VALUES(9)")
    target = database(*ddl, "INSERT INTO a VALUES(2)", "INSERT INTO b VALUES(9)")
    report = evaluate_workload(
        source, target, workload(tmp_path, "SELECT a.id,b.id FROM a JOIN b ON 1=1")
    )
    assert report.overall_result_retention == 0
    assert report.overall_key_retention == 0


@pytest.mark.parametrize("group", ["t.value", "value", "label", "1"])
def test_group_aliases_and_ordinals_map_to_output_positions(database, tmp_path, group):
    ddl = "CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT)"
    source = database(ddl, "INSERT INTO t VALUES(1,'A'),(2,'B')")
    target = database(ddl, "INSERT INTO t VALUES(1,'A')")
    analysis = workload(tmp_path, f"SELECT t.value AS label,COUNT(*) AS n FROM t GROUP BY {group}")
    assert analysis.queries[0].group_columns == [("t", "value")]
    report = evaluate_workload(source, target, analysis)
    assert report.overall_aggregate_retention == 0.5


def test_unprojected_groups_do_not_become_none_keys(database, tmp_path):
    ddl = "CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT)"
    source = database(ddl, "INSERT INTO t VALUES(1,'A'),(2,'A'),(3,'B')")
    target = database(ddl, "INSERT INTO t VALUES(1,'A')")
    report = evaluate_workload(
        source, target, workload(tmp_path, "SELECT COUNT(*) AS n FROM t GROUP BY value")
    )
    assert report.overall_aggregate_retention == 0.5  # Conservative exact-row fallback.


def test_target_failure_remains_in_survival_denominator(database, tmp_path):
    source = database(
        "CREATE TABLE a(id INTEGER PRIMARY KEY)",
        "INSERT INTO a VALUES(1)",
        "CREATE TABLE b(id INTEGER PRIMARY KEY)",
        "INSERT INTO b VALUES(1)",
    )
    target = database("CREATE TABLE a(id INTEGER PRIMARY KEY)", "INSERT INTO a VALUES(1)")
    report = evaluate_workload(
        source, target, workload(tmp_path, "SELECT * FROM a; SELECT * FROM b;", source)
    )
    assert report.supported_queries == 2
    assert report.excluded_queries == 0
    assert report.failed_queries == 1
    assert report.survival_denominator == 2
    assert report.query_survival_rate == 0.5
    assert report.per_query_results[1].error
    assert report.per_query_results[1].source_row_count == 1


def test_source_failure_is_not_disguised_as_unsupported(database, tmp_path):
    source = database("CREATE TABLE a(id INTEGER PRIMARY KEY)", "INSERT INTO a VALUES(1)")
    target = database("CREATE TABLE a(id INTEGER PRIMARY KEY)", "INSERT INTO a VALUES(1)")
    analysis = workload(tmp_path, "SELECT * FROM a; SELECT * FROM missing;", source)
    assert len(analysis.supported_queries) == 2
    report = evaluate_workload(source, target, analysis)
    assert report.failed_queries == 1
    assert report.source_unknown_queries == 1
    assert report.survival_denominator == 2
    assert report.query_survival_rate == 0.5


def test_source_empty_queries_are_excluded_from_survival_only(database, tmp_path):
    ddl = "CREATE TABLE t(id INTEGER PRIMARY KEY)"
    source, target = (
        database(ddl, "INSERT INTO t VALUES(1)"),
        database(ddl, "INSERT INTO t VALUES(1)"),
    )
    report = evaluate_workload(
        source, target, workload(tmp_path, "SELECT * FROM t; SELECT * FROM t WHERE id=-1;")
    )
    assert report.supported_queries == 2
    assert report.source_nonempty_queries == report.survival_denominator == 1
    assert report.query_survival_rate == 1.0


def compile_runs(tmp_path, monkeypatch, runs):
    import safeslicesql.plots

    monkeypatch.setattr(safeslicesql.plots, "generate_all_plots", lambda *args, **kwargs: [])
    directories = [tmp_path / name for name in ["raw", "summary", "plots"]]
    for directory in directories:
        directory.mkdir()
    result = _compile_and_save_summaries(runs, *directories, "test.yaml")
    summary = pd.read_csv(directories[1] / "benchmark_summary.csv")
    markdown = (directories[1] / "benchmark_summary.md").read_text(encoding="utf-8")
    return result, summary, markdown


def run_result(method, success, score, runtime=10):
    return BenchmarkRunResult(
        run_id=f"{method}_{success}",
        method=method,
        seed=42,
        budget_percent=20,
        dataset_size=100,
        success=success,
        runtime_selection=runtime,
        workload=WorkloadReport(
            supported_queries=1, query_survival_rate=score, overall_result_retention=score
        ),
    )


def test_failed_runs_do_not_change_means_or_runtime(tmp_path, monkeypatch):
    result, summary, markdown = compile_runs(
        tmp_path,
        monkeypatch,
        [
            run_result("random-repair", True, 0.8, 10),
            run_result("random-repair", False, 0.0, 0),
            run_result("workload-aware", True, 0.2, 3),
        ],
    )
    row = summary.loc[summary.method == "random-repair"].iloc[0]
    assert row.query_survival_rate_mean == 0.8
    assert row.total_runtime_mean == 10
    assert row.successful_runs == row.failed_runs == 1
    assert row.total_runs == 2
    assert result.failed_runs == 1
    assert "80.0% (random-repair)" in markdown
    assert "strictly superior" not in markdown


def test_all_failed_groups_have_no_performance_measurements(tmp_path, monkeypatch):
    _, summary, markdown = compile_runs(
        tmp_path, monkeypatch, [run_result("workload-aware", False, 0.9)]
    )
    assert pd.isna(summary.iloc[0].query_survival_rate_mean)
    assert pd.isna(summary.iloc[0].total_runtime_mean)
    assert summary.iloc[0].failed_runs == 1
    assert summary.iloc[0].query_survival_rate_count == 0
    assert "no successful query-survival measurements" in markdown
    assert "N/A" in markdown


def test_tied_results_report_both_methods(tmp_path, monkeypatch):
    _, _, markdown = compile_runs(
        tmp_path,
        monkeypatch,
        [run_result("random-repair", True, 0.5), run_result("workload-aware", True, 0.5)],
    )
    assert "50.0% (random-repair, workload-aware)" in markdown


def test_absent_workload_is_not_reported_as_zero_retention(tmp_path, monkeypatch):
    run = run_result("random-repair", True, 0)
    run.workload = None
    _, summary, markdown = compile_runs(tmp_path, monkeypatch, [run])
    assert pd.isna(summary.iloc[0].query_survival_rate_mean)
    assert summary.iloc[0].query_survival_rate_count == 0
    assert summary.iloc[0].total_runtime_mean == 10
    assert "no successful query-survival measurements" in markdown


def test_all_failed_plot_data_removes_stale_generated_charts(tmp_path):
    from safeslicesql.plots import generate_all_plots

    csv_path = tmp_path / "summary.csv"
    pd.DataFrame([{"method": "random-repair", "budget_percent": 20, "successful_runs": 0}]).to_csv(
        csv_path, index=False
    )
    stale = tmp_path / "query_survival_vs_budget.png"
    stale.write_bytes(b"old chart")
    unrelated = tmp_path / "custom.png"
    unrelated.write_bytes(b"user chart")
    assert generate_all_plots(csv_path, tmp_path) == []
    assert not stale.exists()
    assert unrelated.read_bytes() == b"user chart"


def test_benchmark_marks_workload_errors_as_failed(database, tmp_path, monkeypatch):
    import safeslicesql.plots

    monkeypatch.setattr(safeslicesql.plots, "generate_all_plots", lambda *args, **kwargs: [])
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    target = database()
    query_file = tmp_path / "bad.sql"
    query_file.write_text("SELECT missing_column FROM t", encoding="utf-8")
    config = tmp_path / "benchmark.yaml"
    config.write_text(
        f"matrix:\n  methods: [random-repair]\n  budget_percents: [100]\n  seeds: [42]\n"
        f"workload:\n  path: '{query_file.as_posix()}'\n"
        f"output:\n  raw_dir: '{(tmp_path / 'raw').as_posix()}'\n  summary_dir: '{(tmp_path / 'summary').as_posix()}'\n  plots_dir: '{(tmp_path / 'plots').as_posix()}'\n",
        encoding="utf-8",
    )
    result = run_benchmark(config, source, target)
    assert result.failed_runs == 1
    assert result.runs[0].workload.failed_queries == 1
    assert result.runs[0].validation.source_unchanged is True
