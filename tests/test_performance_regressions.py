"""Regression coverage for bounded selection and exact comparison optimizations."""

import random

import pytest

from safeslicesql import sampler
from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.result_store import temporary_result_store
from safeslicesql.types import ColumnMetadata, SchemaMetadata, TableMetadata
from safeslicesql.workload_analyzer import QueryInfo, WorkloadAnalysis


def independent_schema(count=20):
    return SchemaMetadata(
        database_name="test",
        tables={
            "items": TableMetadata(
                name="items",
                primary_key="id",
                row_count=count,
                columns=[ColumnMetadata("id", "INTEGER", False, is_primary_key=True)],
            )
        },
    )


def workload():
    query = QueryInfo("Q1", "SELECT * FROM items")
    query.tables = ["items"]
    analysis = WorkloadAnalysis()
    analysis.queries = [query]
    return analysis


@pytest.mark.parametrize("method", sampler.METHODS)
def test_zero_budget_never_reads_database(method):
    class NoReads:
        def connect(self):
            pytest.fail("Zero-budget selection must not open a database connection")

    result = sampler.select_rows(
        independent_schema(), workload(), engine=NoReads(), budget=0, method=method
    )
    assert result.total_selected == 0
    assert not result.selected_rows


def test_independent_candidate_skips_dependency_traversal(monkeypatch):
    schema = independent_schema()

    def unexpected(*args, **kwargs):
        pytest.fail("Independent rows need no graph traversal")

    monkeypatch.setattr(sampler, "compute_closure", unexpected)
    graph = build_dependency_graph(schema)
    assert sampler._candidate_closure(graph, None, "items", 2, {"items": {1}}, 1) == {"items": {2}}
    assert sampler._candidate_closure(graph, None, "items", 1, {"items": {1}}, 0) == {
        "items": set()
    }


@pytest.mark.parametrize("method", sampler.METHODS)
def test_selection_total_matches_actual_rows_without_recounting(method, monkeypatch):
    schema = independent_schema()

    def unexpected(*args):
        pytest.fail("Selection hot loop should maintain its count incrementally")

    monkeypatch.setattr(sampler, "_selection_total", unexpected)
    rows = {"items": {key: {"id": key} for key in range(20)}}
    result = sampler.select_rows(schema, workload(), engine=rows, budget=7, method=method)
    assert result.total_selected == sum(map(len, result.selected_rows.values())) == 7


def test_weight_buckets_preserve_seeded_priority_order(monkeypatch):
    schema = independent_schema()
    schema.tables["other"] = TableMetadata(
        name="other",
        primary_key="id",
        row_count=20,
        columns=[ColumnMetadata("id", "INTEGER", False, is_primary_key=True)],
    )
    candidates = {"items": list(range(20)), "other": list(range(20))}
    monkeypatch.setattr(sampler, "_candidate_rows_by_table", lambda *args: candidates.copy())
    monkeypatch.setattr(sampler, "query_witnesses", lambda *args: [])
    expected = [
        (2.0 if table == "items" else 1.0, table, key)
        for table, keys in candidates.items()
        for key in keys
    ]
    random.Random(42).shuffle(expected)
    expected.sort(key=lambda row: row[0], reverse=True)
    result = sampler.select_rows(schema, workload(), engine={}, budget=23, method="workload-aware")
    assert {table: set(keys) for table, keys in result.selected_rows.items()} == {
        table: {key for _, name, key in expected[:23] if name == table} for table in candidates
    }


def test_duplicate_comparison_batches_preserve_counts_with_fewer_writes():
    with temporary_result_store() as store:
        statements = []
        store.connection.set_trace_callback(statements.append)
        store.add_batch([(0, "duplicate", 1, 0)] * 100 + [(1, "key", 1, 0)] * 10)
        store.add_batch([(0, "duplicate", 0, 1)] * 40 + [(1, "key", 0, 1)])
        assert store.retention(0, multiset=True) == 0.4
        assert store.retention(1) == 1.0
        assert sum(sql.startswith("INSERT") for sql in statements) == 4
