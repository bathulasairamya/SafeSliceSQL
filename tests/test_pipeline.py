"""Failure boundaries of the runner shared by CLI, benchmarks, and desktop."""

from unittest.mock import Mock

import pytest
import sqlalchemy as sa

import safeslicesql.pipeline as pipeline
from safeslicesql.automatic_workload import save_automatic_workload
from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.source_snapshot import capture_source_snapshot
from safeslicesql.types import GenerationResult
from safeslicesql.workload_analyzer import analyze_workload


@pytest.fixture
def options(tmp_path):
    source, target = sa.create_engine("sqlite://"), sa.create_engine("sqlite://")
    with source.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE t(id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO t VALUES(1)")
    schema = inspect_schema(source)
    workload = analyze_workload(save_automatic_workload(schema, tmp_path), source)
    yield dict(
        source_engine=source,
        target_engine=target,
        schema=schema,
        graph=build_dependency_graph(schema),
        workload=workload,
        budget=1,
        method="random-repair",
        seed=42,
        run_id="test",
        budget_percent=100,
        target_database="target",
        initial_source_snapshot=capture_source_snapshot(source),
    )
    source.dispose()
    target.dispose()


def test_shared_runner_preserves_stage_order_and_validates_copy(options):
    completed = []
    result = pipeline.run_pipeline(**options, on_stage=lambda name, _: completed.append(name))
    assert result.success, result.error_message
    assert completed == ["selection", "generation", "workload", "validation"]
    assert result.validation.source_unchanged is True
    assert result.validation.actual_rows == 1
    assert result.workload.overall_result_retention == 1


def test_failed_materialization_never_evaluates_stale_target(options, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "materialize_subset",
        lambda **kwargs: GenerationResult("target", error_message="copy failed"),
    )
    evaluate = Mock(side_effect=AssertionError("must not evaluate a failed copy"))
    monkeypatch.setattr(pipeline, "evaluate_workload", evaluate)
    result = pipeline.run_pipeline(**options)
    assert not result.success
    assert result.error_message == "copy failed"
    assert result.validation is None
    evaluate.assert_not_called()


def test_workload_exception_still_checks_source_and_fails_run(options, monkeypatch):
    monkeypatch.setattr(
        pipeline, "evaluate_workload", Mock(side_effect=RuntimeError("query failed"))
    )
    result = pipeline.run_pipeline(**options)
    assert not result.success
    assert "query failed" in result.error_message
    assert result.validation.is_valid
    assert result.validation.source_unchanged is True
