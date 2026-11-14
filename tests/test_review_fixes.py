"""Regression checks for the project review, including rollback and scaling work."""

import random
import threading
import time
from unittest.mock import Mock

import pytest
import sqlalchemy as sa

from safeslicesql.acceptance import AcceptancePolicy
from safeslicesql.automatic_workload import generate_workload
from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.pipeline import run_pipeline
from safeslicesql.runtime import LimitExceeded, Limits, execution_context
from safeslicesql.sampler import _sample_primary_keys, select_rows
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.schema_compatibility import table_schema_differences
from safeslicesql.source_snapshot import capture_source_snapshot
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.types import SelectionResult
from safeslicesql.validator import evaluate_workload
from safeslicesql.workload_analyzer import analyze_workload


@pytest.fixture
def pair():
    engines = [sa.create_engine("sqlite://") for _ in range(2)]
    for engine in engines:
        with engine.begin() as c:
            c.exec_driver_sql("CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT)")
    with engines[0].begin() as c:
        c.exec_driver_sql("INSERT INTO t VALUES(1,'one'),(2,'two')")
    with engines[1].begin() as c:
        c.exec_driver_sql("INSERT INTO t VALUES(99,'previous')")
    yield engines
    for engine in engines:
        engine.dispose()


def workload(tmp_path, source, sql):
    path = tmp_path / "query.sql"
    path.write_text(sql, encoding="utf-8")
    return analyze_workload(path, source)


def options(pair, tmp_path, sql="SELECT * FROM t"):
    source, target = pair
    schema = inspect_schema(source)
    return dict(
        source_engine=source,
        target_engine=target,
        schema=schema,
        graph=build_dependency_graph(schema),
        workload=workload(tmp_path, source, sql),
        budget=1,
        method="random-repair",
        seed=42,
        run_id="review",
        budget_percent=50,
        target_database="target",
        initial_source_snapshot=capture_source_snapshot(source),
    )


def target_rows(target):
    with target.connect() as c:
        return c.exec_driver_sql("SELECT * FROM t ORDER BY id").all()


def test_rejected_acceptance_keeps_previous_target(pair, tmp_path):
    result = run_pipeline(
        **options(pair, tmp_path), acceptance=AcceptancePolicy(min_result_retention=1)
    )
    assert not result.success
    assert result.acceptance and not result.acceptance.passed
    assert result.generation.total_rows_inserted == 0
    assert target_rows(pair[1]) == [(99, "previous")]


def test_target_trigger_is_rejected_before_rows_change(pair, tmp_path):
    with pair[1].begin() as c:
        c.exec_driver_sql(
            "CREATE TRIGGER corrupt AFTER INSERT ON t BEGIN UPDATE t SET value='changed' WHERE id=NEW.id; END"
        )
    result = run_pipeline(**options(pair, tmp_path, "SELECT id FROM t"))
    assert not result.success and "trigger" in result.error_message.lower()
    assert target_rows(pair[1]) == [(99, "previous")]


def test_missing_index_is_not_compatible(pair):
    with pair[0].begin() as c:
        c.exec_driver_sql("CREATE INDEX ix_value ON t(value)")
    assert "indexes" in " ".join(table_schema_differences(*(sa.inspect(e) for e in pair), "t"))


def test_identical_partial_indexes_compare_by_definition(pair):
    for engine in pair:
        with engine.begin() as c:
            c.exec_driver_sql("CREATE INDEX ix_value ON t(value) WHERE value IS NOT NULL")
    assert table_schema_differences(*(sa.inspect(e) for e in pair), "t") == []


def test_benchmark_reuses_one_source_fingerprint(pair, tmp_path):
    import yaml

    from safeslicesql.benchmark import run_benchmark

    path = tmp_path / "query.sql"
    path.write_text("SELECT id FROM t", encoding="utf-8")
    config = tmp_path / "benchmark.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "matrix": {
                    "methods": ["random-repair", "referential", "workload-aware"],
                    "budget_percents": [100],
                    "seeds": [42],
                },
                "workload": {"path": str(path)},
                "output": {
                    name: str(tmp_path / name) for name in ("raw_dir", "summary_dir", "plots_dir")
                },
            }
        ),
        encoding="utf-8",
    )
    scans = []

    @sa.event.listens_for(pair[0], "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, many):
        if statement == "SELECT * FROM `t`":
            scans.append(statement)

    result = run_benchmark(config, *pair, target_database_name="target")
    assert result.successful_runs == 3
    assert len(scans) == 1


def test_predicate_witnesses_change_selection(pair, tmp_path):
    source = pair[0]
    schema = inspect_schema(source)
    for key in (1, 2):
        analysis = workload(tmp_path, source, f"SELECT id FROM t WHERE id={key}")
        result = select_rows(schema, analysis, engine=source, budget=1, method="workload-aware")
        assert result.selected_rows == {"t": [key]}


def test_join_witness_includes_both_participants(pair, tmp_path):
    source = pair[0]
    with source.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE child(id INTEGER PRIMARY KEY,parent INTEGER REFERENCES t(id))"
        )
        c.exec_driver_sql("INSERT INTO child VALUES(10,2)")
    analysis = workload(
        tmp_path, source, "SELECT c.id FROM child c JOIN t p ON c.parent=p.id WHERE p.value='two'"
    )
    result = select_rows(
        inspect_schema(source), analysis, engine=source, budget=2, method="workload-aware"
    )
    assert result.selected_rows == {"child": [10], "t": [2]}


@pytest.mark.parametrize(
    "sql", ["SELECT t.id FROM t, external.secret s", "SELECT t.id FROM t AS a, t AS b"]
)
def test_comma_joins_fail_closed(tmp_path, sql):
    assert not workload(tmp_path, None, sql).queries[0].is_supported


def test_ui_limit_exception_reaches_error_callback(monkeypatch):
    from safeslicesql import view_tasks

    completed = threading.Event()
    callbacks = []
    widget = Mock()
    widget.after.side_effect = lambda delay, action: callbacks.append(action)
    widget.winfo_exists.return_value = True

    def task():
        completed.set()
        raise LimitExceeded("limit reached")

    failed = Mock()
    view_tasks.background(widget, task, Mock(), failed)
    assert completed.wait(2)
    deadline = time.monotonic() + 2
    while callbacks and not failed.called and time.monotonic() < deadline:
        callbacks.pop(0)()
        time.sleep(0.001)
    failed.assert_called_once_with("limit reached")


def test_interrupt_after_ddl_removes_new_tables(pair):
    source = pair[0]
    target = sa.create_engine("sqlite://")
    schema = inspect_schema(source)

    def interrupt(*_):
        raise LimitExceeded("stop before commit")

    try:
        with pytest.raises(LimitExceeded):
            materialize_subset(
                source,
                target,
                schema,
                SelectionResult("test", 42, 1, {"t": [1]}),
                build_dependency_graph(schema),
                "target",
                before_commit=interrupt,
            )
        assert sa.inspect(target).get_table_names() == []
    finally:
        target.dispose()


def test_dense_integer_sampling_avoids_full_scan(pair):
    source = pair[0]
    with source.begin() as c:
        c.exec_driver_sql("DELETE FROM t")
        c.execute(sa.text("INSERT INTO t VALUES(:id,'value')"), [{"id": i} for i in range(10000)])
    schema = inspect_schema(source)
    statements = []

    @sa.event.listens_for(source, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    keys = _sample_primary_keys(schema, "t", source, 50, random.Random(42))
    assert len(set(keys)) == 50
    assert not any("ORDER BY" in sql for sql in statements)
    assert len(statements) <= 3


def test_key_retention_uses_declared_key_even_when_aliased(pair, tmp_path):
    source, target = pair
    with target.begin() as c:
        c.exec_driver_sql("DELETE FROM t")
        c.exec_driver_sql("INSERT INTO t VALUES(1,'changed')")
    report = evaluate_workload(
        source, target, workload(tmp_path, source, "SELECT id AS unusual_alias,value FROM t")
    )
    result = report.per_query_results[0]
    assert not result.error
    assert result.key_retention == 0.5
    assert result.result_row_retention == 0


def test_comparison_cost_is_separate_from_fetch_time(pair, tmp_path, monkeypatch):
    from safeslicesql.result_store import ResultStore

    original = ResultStore.add_batch

    def slow(self, rows):
        time.sleep(0.04)
        return original(self, rows)

    monkeypatch.setattr(ResultStore, "add_batch", slow)
    result = evaluate_workload(
        *pair, workload(tmp_path, pair[0], "SELECT * FROM t")
    ).per_query_results[0]
    assert result.comparison_time_source >= 0.04
    assert result.execution_time_source < result.comparison_time_source


def test_automatic_workload_respects_result_cap(pair, tmp_path):
    schema = inspect_schema(pair[0])
    with execution_context(Limits(max_result_rows=1)):
        sql = generate_workload(schema)
        assert "LIMIT 1" in sql
        analysis = workload(tmp_path, pair[0], sql)
        assert all(not q.analysis_error and q.source_row_count <= 1 for q in analysis.queries)
