"""Regression coverage for audit issues 8-15."""

import pytest
import sqlalchemy as sa

from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.sampler import select_rows
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.types import SelectionResult
from safeslicesql.workload_analyzer import (
    QueryInfo,
    WorkloadAnalysis,
    analyze_workload,
    load_workload,
)


@pytest.fixture
def database():
    engines = []

    def create(*statements):
        engine = sa.create_engine("sqlite:///:memory:")
        engines.append(engine)

        @sa.event.listens_for(engine, "connect")
        def enforce(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
        return engine

    yield create
    for engine in engines:
        engine.dispose()


def materialize(source, target, keys, budget=100, chunk_size=500):
    schema = inspect_schema(source)
    return materialize_subset(
        source,
        target,
        schema,
        SelectionResult("test", 42, budget, keys),
        build_dependency_graph(schema),
        "target",
        chunk_size,
    )


def rows(engine, table="t"):
    with engine.connect() as connection:
        return connection.exec_driver_sql(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


@pytest.mark.parametrize("chunk_size", [1, 2, 500])
def test_self_references_insert_parents_and_delete_children_first(database, chunk_size):
    ddl = "CREATE TABLE t(id INTEGER PRIMARY KEY,parent INTEGER REFERENCES t(id))"
    source = database(ddl, "INSERT INTO t VALUES(30,NULL),(20,30),(1,20)")
    target = database(ddl, "INSERT INTO t VALUES(99,NULL),(10,99)")
    result = materialize(source, target, {"t": [1, 20, 30]}, chunk_size=chunk_size)
    assert result.success, result.error_message
    assert rows(target) == rows(source)
    # Repeated refresh must also delete the just-created hierarchy safely.
    result = materialize(source, target, {"t": [30]}, chunk_size=chunk_size)
    assert result.success, result.error_message
    assert rows(target) == [(30, None)]


def test_self_reference_to_unique_column_with_composite_pk(database):
    source = database(
        "CREATE TABLE t(a INTEGER,b INTEGER,code TEXT UNIQUE,parent TEXT REFERENCES t(code),PRIMARY KEY(a,b))",
        "INSERT INTO t VALUES(9,9,'root',NULL),(1,1,'child','root')",
    )
    target = database()
    result = materialize(source, target, {"t": [(1, 1), (9, 9)]}, chunk_size=1)
    assert result.success, result.error_message
    assert rows(target) == rows(source)


@pytest.mark.parametrize("cycle_in_target", [False, True])
def test_cyclic_self_references_fail_without_losing_old_rows(database, cycle_in_target):
    ddl = "CREATE TABLE t(id INTEGER PRIMARY KEY,parent INTEGER REFERENCES t(id))"
    source = database(ddl, "INSERT INTO t VALUES(1,NULL),(2,1)")
    target = database(ddl, "INSERT INTO t VALUES(99,NULL),(100,99)")
    with (target if cycle_in_target else source).begin() as connection:
        connection.exec_driver_sql(
            "UPDATE t SET parent=100 WHERE id=99"
            if cycle_in_target
            else "UPDATE t SET parent=2 WHERE id=1"
        )
    before = rows(target)
    result = materialize(source, target, {"t": [1, 2]})
    assert not result.success
    assert "cycle" in result.error_message
    assert rows(target) == before
    assert result.total_rows_inserted == 0


def test_unselected_self_parent_fails_safely(database):
    ddl = "CREATE TABLE t(id INTEGER PRIMARY KEY,parent INTEGER REFERENCES t(id))"
    source = database(ddl, "INSERT INTO t VALUES(2,NULL),(1,2)")
    target = database(ddl, "INSERT INTO t VALUES(99,NULL)")
    result = materialize(source, target, {"t": [1]})
    assert not result.success
    assert "parent" in result.error_message
    assert rows(target) == [(99, None)]


@pytest.mark.parametrize("method", ["random-repair", "referential", "workload-aware"])
def test_no_primary_key_rejected_by_all_selectors(database, method):
    source = database(
        "CREATE TABLE t(id INTEGER,value TEXT)", "INSERT INTO t VALUES(1,'a'),(1,'b')"
    )
    with pytest.raises(ValueError, match="primary key"):
        select_rows(inspect_schema(source), engine=source, budget=1, method=method)


def test_no_primary_key_cannot_overfill_target(database):
    source = database(
        "CREATE TABLE t(id INTEGER,value TEXT)", "INSERT INTO t VALUES(1,'a'),(1,'b')"
    )
    target = database("CREATE TABLE t(id INTEGER,value TEXT)", "INSERT INTO t VALUES(99,'old')")
    result = materialize(source, target, {"t": [1]}, budget=1)
    assert not result.success
    assert "primary key" in result.error_message
    assert rows(target) == [(99, "old")]


@pytest.mark.parametrize("storage", ["STORED", "VIRTUAL"])
def test_generated_columns_are_recomputed_on_target(database, storage):
    source = database(
        f"CREATE TABLE t(id INTEGER PRIMARY KEY,value INTEGER,doubled INTEGER GENERATED ALWAYS AS (value*2) {storage})",
        "INSERT INTO t(id,value) VALUES(1,3),(2,5)",
    )
    target = database()
    result = materialize(source, target, {"t": [2, 1]})
    assert result.success, result.error_message
    assert rows(target) == [(1, 3, 6), (2, 5, 10)]


@pytest.mark.parametrize("budget", [-1, 0])
def test_nonempty_selection_cannot_bypass_budget(database, budget):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    target = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(99)")
    result = materialize(source, target, {"t": [1]}, budget=budget)
    assert not result.success
    assert "budget" in result.error_message.lower()
    assert rows(target) == [(99,)]


def test_zero_budget_empty_selection_is_valid(database):
    ddl = "CREATE TABLE t(id INTEGER PRIMARY KEY)"
    source, target = (
        database(ddl, "INSERT INTO t VALUES(1)"),
        database(ddl, "INSERT INTO t VALUES(99)"),
    )
    assert select_rows(inspect_schema(source), engine=source, budget=0).total_selected == 0
    result = materialize(source, target, {}, budget=0)
    assert result.success, result.error_message
    assert rows(target) == []
    assert result.total_rows_inserted == 0


@pytest.mark.parametrize("budget", [-1, 1.5, True])
def test_invalid_selector_budgets_are_rejected(database, budget):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="non-negative integer"):
        select_rows(inspect_schema(source), engine=source, budget=budget)


@pytest.mark.parametrize("composite", [False, True])
def test_missing_rows_rollback_earlier_chunks_and_report_zero(database, composite):
    ddl = (
        "CREATE TABLE t(a INTEGER,b INTEGER,PRIMARY KEY(a,b))"
        if composite
        else "CREATE TABLE t(id INTEGER PRIMARY KEY)"
    )
    source = database(ddl, "INSERT INTO t VALUES(1,1)" if composite else "INSERT INTO t VALUES(1)")
    target = database(
        ddl, "INSERT INTO t VALUES(99,99)" if composite else "INSERT INTO t VALUES(99)"
    )
    before = rows(target)
    result = materialize(
        source, target, {"t": [(1, 1), (2, 2)] if composite else [1, 2]}, chunk_size=1
    )
    assert not result.success
    assert "missing" in result.error_message
    assert rows(target) == before
    assert result.total_rows_inserted == 0
    assert result.rows_inserted == {}


@pytest.mark.parametrize("selection", [{"t": [1, 1]}, {"unknown": [1]}, {"t": [None]}])
def test_invalid_selection_is_rejected_before_writes(database, selection):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    target = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(99)")
    assert not materialize(source, target, selection).success
    assert rows(target) == [(99,)]


def test_actual_target_budget_includes_trigger_rows(database):
    ddl = "CREATE TABLE t(id INTEGER PRIMARY KEY)"
    source = database(ddl, "INSERT INTO t VALUES(1)")
    target = database(
        ddl,
        "INSERT INTO t VALUES(99)",
        "CREATE TRIGGER extra AFTER INSERT ON t WHEN NEW.id=1 BEGIN INSERT INTO t VALUES(2); END",
    )
    result = materialize(source, target, {"t": [1]}, budget=1)
    assert not result.success
    assert "budget" in result.error_message
    assert rows(target) == [(99,)]


def test_comments_and_semicolons_preserve_query_semantics(database, tmp_path):
    source = database(
        "CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT)",
        "INSERT INTO t VALUES(1,'a;b--c#d/*e*/'),(2,'other')",
    )
    path = tmp_path / "workload.sql"
    path.write_text(
        "\ufeff-- ignored;\nSELECT * FROM t -- inline; comment\nWHERE value='a;b--c#d/*e*/'; /* ignored; */\nSELECT/**/id FROM t # comment;\nWHERE id=2;",
        encoding="utf-8",
    )
    analysis = analyze_workload(path, source)
    assert len(analysis.queries) == 2
    assert len(analysis.supported_queries) == 2
    assert [query.source_row_count for query in analysis.queries] == [1, 1]
    assert "WHERE value='a;b--c#d/*e*/'" in analysis.queries[0].raw_sql


@pytest.mark.parametrize("literal", ["'it''s;a'", r"'it\'s;a'", '"a;--b"'])
def test_quoted_escapes_do_not_split_statements(tmp_path, literal):
    path = tmp_path / "workload.sql"
    statement = f"SELECT {literal} FROM t"
    path.write_text(statement + "; SELECT id FROM t;", encoding="utf-8")
    assert load_workload(path) == [statement, "SELECT id FROM t"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'unterminated FROM t",
        "SELECT * FROM t /* unterminated",
        "SELECT /*! executable */ * FROM t",
    ],
)
def test_ambiguous_workload_fails_before_execution(tmp_path, sql):
    path = tmp_path / "workload.sql"
    path.write_text(sql, encoding="utf-8")
    with pytest.raises(ValueError):
        load_workload(path)


@pytest.mark.parametrize("table", ["t", "odd table", "odd`name", "with;semicolon", "select"])
def test_quoted_aggregate_tables_are_resolved_without_crashing(database, tmp_path, table):
    quoted = "`" + table.replace("`", "``") + "`"
    source = database(
        f"CREATE TABLE {quoted}(id INTEGER PRIMARY KEY)", f"INSERT INTO {quoted} VALUES(1)"
    )
    path = tmp_path / "workload.sql"
    path.write_text(f"SELECT COUNT(*) FROM {quoted};", encoding="utf-8")
    analysis = analyze_workload(path, source)
    assert analysis.queries[0].is_supported
    assert analysis.queries[0].tables == [table]
    result = select_rows(
        inspect_schema(source), analysis, engine=source, budget=1, method="workload-aware"
    )
    assert result.selected_rows == {table: [1]}


def test_manually_supplied_query_without_tables_cannot_crash_sampler(database):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    workload = WorkloadAnalysis()
    query = QueryInfo("Q1", "SELECT COUNT(*) FROM t")
    query.aggregate_functions = ["COUNT"]
    workload.queries = [query]
    with pytest.raises(ValueError, match="usable workload query"):
        select_rows(
            inspect_schema(source), workload, engine=source, budget=1, method="workload-aware"
        )
