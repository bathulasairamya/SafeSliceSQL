"""Regression coverage for audit issues 1-7: source safety and atomic refresh."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

from safeslicesql.db import assert_distinct_databases
from safeslicesql.dependency_graph import build_dependency_graph, clear_fk_cache, compute_closure
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.types import DatabaseResolutionError, SelectionResult
from safeslicesql.validator import validate_subset
from safeslicesql.workload_analyzer import analyze_workload


@pytest.fixture
def database():
    engines = []

    def create(*statements):
        engine = sa.create_engine("sqlite:///:memory:")
        engines.append(engine)

        @sa.event.listens_for(engine, "connect")
        def enable_constraints(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
        return engine

    yield create
    clear_fk_cache()
    for engine in engines:
        engine.dispose()


def materialize(source, target, rows):
    schema = inspect_schema(source)
    return materialize_subset(
        source,
        target,
        schema,
        SelectionResult("test", 42, 100, rows),
        build_dependency_graph(schema),
        "target_label",
    )


def rows(engine, table):
    with engine.connect() as connection:
        return connection.exec_driver_sql(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE t",
        "DELETE FROM t",
        "UPDATE t SET value='changed'",
        "CREATE TABLE injected (id INTEGER)",
        "SELECT t.id FROM t LEFT JOIN t other ON t.id=other.id",
    ],
)
def test_rejected_workload_never_executes(database, tmp_path, sql):
    source = database(
        "CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT)", "INSERT INTO t VALUES(1,'original')"
    )
    workload = tmp_path / "workload.sql"
    workload.write_text(sql + ";", encoding="utf-8")
    executed = []

    @sa.event.listens_for(source, "before_cursor_execute")
    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        executed.append(statement)

    analysis = analyze_workload(workload, source)
    assert not analysis.queries[0].is_supported
    assert sql not in executed
    assert rows(source, "t") == [(1, "original")]
    assert not sa.inspect(source).has_table("injected")


def test_real_source_identity_cannot_be_hidden_by_label(database):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    with pytest.raises(ValueError, match="identical"):
        materialize(source, source, {"t": [1]})
    with pytest.raises(ValueError, match="identical"):
        assert_distinct_databases(source, source.execution_options(), "other")
    assert rows(source, "t") == [(1,)]


def test_sqlite_file_identity_uses_connected_path(tmp_path):
    path = tmp_path / "source.db"
    source = sa.create_engine(f"sqlite:///{path}")
    target = sa.create_engine(f"sqlite:///{path.parent / '.' / path.name}")
    try:
        with pytest.raises(ValueError, match="identical"):
            assert_distinct_databases(source, target, "other")
    finally:
        source.dispose()
        target.dispose()


def mysql_engine(host, server, database_name, case_sensitive=0):
    connection = MagicMock()
    connection.execute.return_value.one.return_value = (server, database_name, case_sensitive)
    context = MagicMock()
    context.__enter__.return_value = connection
    return SimpleNamespace(
        dialect=SimpleNamespace(name="mysql"),
        pool=object(),
        url=sa.engine.make_url(f"mysql+pymysql://user@{host}/{database_name}"),
        connect=lambda: context,
    )


def test_mysql_host_aliases_cannot_bypass_identity_check():
    source = mysql_engine("localhost", "server-uuid", "production")
    target = mysql_engine("127.0.0.1", "server-uuid", "production")
    with pytest.raises(ValueError, match="identical"):
        assert_distinct_databases(source, target, "other")


def test_mysql_target_name_must_match_connection():
    source = mysql_engine("source", "server-uuid", "production")
    target = mysql_engine("target", "server-uuid", "subset")
    with pytest.raises(ValueError, match="does not match"):
        assert_distinct_databases(source, target, "other")
    assert_distinct_databases(source, target, "subset")


def test_mysql_case_insensitive_database_identity():
    source = mysql_engine("localhost", "server-uuid", "Production", 1)
    target = mysql_engine("127.0.0.1", "server-uuid", "production", 1)
    with pytest.raises(ValueError, match="identical"):
        assert_distinct_databases(source, target, "production")


@pytest.mark.parametrize(
    "target_ddl",
    [
        "CREATE TABLE t(id INTEGER, value TEXT NOT NULL DEFAULT 'x' UNIQUE CHECK(length(value)>0))",
        "CREATE TABLE t(id INTEGER PRIMARY KEY, value INTEGER NOT NULL DEFAULT 'x' UNIQUE CHECK(length(value)>0))",
        "CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT DEFAULT 'x' UNIQUE CHECK(length(value)>0))",
        "CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT NOT NULL DEFAULT 'y' UNIQUE CHECK(length(value)>0))",
        "CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT NOT NULL DEFAULT 'x' CHECK(length(value)>0))",
        "CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT NOT NULL DEFAULT 'x' UNIQUE)",
    ],
)
def test_schema_drift_fails_validation(database, target_ddl):
    source = database(
        "CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT NOT NULL DEFAULT 'x' UNIQUE CHECK(length(value)>0))"
    )
    target = database(target_ddl)
    report = validate_subset(source, target, 10)
    assert not report.is_valid
    assert not report.schema_compatible
    assert report.errors


def test_missing_fk_and_orphans_cannot_pass_validation(database):
    source = database(
        "CREATE TABLE p(id INTEGER PRIMARY KEY)",
        "CREATE TABLE c(id INTEGER PRIMARY KEY,pid INTEGER REFERENCES p(id))",
    )
    target = database(
        "CREATE TABLE p(id INTEGER PRIMARY KEY)",
        "CREATE TABLE c(id INTEGER,pid INTEGER)",
        "INSERT INTO c VALUES(1,999),(1,999)",
    )
    report = validate_subset(source, target, 10)
    assert not report.is_valid
    assert any("foreign keys" in error for error in report.errors)
    assert any("primary keys" in error for error in report.errors)
    assert not report.pk_unique
    assert report.fk_violations == 2


@pytest.mark.parametrize(
    "failing_sql", ["HAVING COUNT(*) > 1", "LEFT JOIN", "PRAGMA main.table_xinfo"]
)
def test_validation_query_failures_fail_closed(database, failing_sql):
    ddl = [
        "CREATE TABLE p(id INTEGER PRIMARY KEY)",
        "CREATE TABLE c(id INTEGER PRIMARY KEY,pid INTEGER REFERENCES p(id))",
    ]
    source, target = database(*ddl), database(*ddl)

    @sa.event.listens_for(target, "before_cursor_execute")
    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        if failing_sql in statement:
            raise RuntimeError("injected inspection/check failure")

    report = validate_subset(source, target, 10)
    assert not report.is_valid
    assert any("injected" in error for error in report.errors)


@pytest.mark.parametrize("composite", [False, True])
def test_non_pk_reference_resolves_actual_parent_key(database, composite):
    definition = (
        "id INTEGER, part INTEGER, code TEXT UNIQUE, PRIMARY KEY(id,part)"
        if composite
        else "id INTEGER PRIMARY KEY, code TEXT UNIQUE"
    )
    source = database(
        f"CREATE TABLE p({definition})",
        "CREATE TABLE c(id INTEGER PRIMARY KEY,pcode TEXT REFERENCES p(code))",
        "INSERT INTO p VALUES(7,8,'X')" if composite else "INSERT INTO p VALUES(7,'X')",
        "INSERT INTO c VALUES(2,'X')",
    )
    closure = compute_closure(build_dependency_graph(inspect_schema(source)), {"c": {2}}, source)
    assert closure.selected_rows["p"] == ({(7, 8)} if composite else {7})
    target = database()
    result = materialize(
        source, target, {name: list(keys) for name, keys in closure.selected_rows.items()}
    )
    assert result.success, result.error_message
    assert validate_subset(source, target, 10).is_valid


def test_reference_lookup_respects_database_collation(database):
    source = database(
        "CREATE TABLE p(id INTEGER PRIMARY KEY, code TEXT COLLATE NOCASE UNIQUE)",
        "CREATE TABLE c(id INTEGER PRIMARY KEY,pcode TEXT REFERENCES p(code))",
        "INSERT INTO p VALUES(7,'ABC')",
        "INSERT INTO c VALUES(2,'abc')",
    )
    closure = compute_closure(build_dependency_graph(inspect_schema(source)), {"c": {2}}, source)
    assert closure.selected_rows["p"] == {7}


def test_non_pk_reference_dictionary_and_missing_parent(database):
    source = database(
        "CREATE TABLE p(id INTEGER PRIMARY KEY,code TEXT UNIQUE)",
        "CREATE TABLE c(id INTEGER PRIMARY KEY,pcode TEXT REFERENCES p(code))",
    )
    graph = build_dependency_graph(inspect_schema(source))
    data = {"p": {7: {"code": "X"}}, "c": {2: {"pcode": "X"}}}
    assert compute_closure(graph, {"c": {2}}, data).selected_rows["p"] == {7}
    data["p"] = {}
    with pytest.raises(DatabaseResolutionError, match="exactly one"):
        compute_closure(graph, {"c": {2}}, data)


def test_exact_types_survive_schema_reproduction(database):
    source = database(
        "CREATE TABLE t(id INTEGER PRIMARY KEY, amount DECIMAL(18,4), happened DATETIME, payload BLOB, description VARCHAR(1000))"
    )
    target = database()
    result = materialize(source, target, {})
    assert result.success, result.error_message
    assert validate_subset(source, target, 10).schema_compatible


def test_incompatible_existing_schema_is_preserved(database):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY,value INTEGER)")
    target = database(
        "CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT)", "INSERT INTO t VALUES(9,'original')"
    )
    result = materialize(source, target, {})
    assert not result.success
    assert "incompatible" in result.error_message
    assert rows(target, "t") == [(9, "original")]


def test_reflection_failure_preserves_existing_data(database, monkeypatch):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    target = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(99)")

    def fail_reflection(*_args, **_kwargs):
        raise RuntimeError("injected reflection failure")

    monkeypatch.setattr(sa.MetaData, "reflect", fail_reflection)
    result = materialize(source, target, {})
    assert not result.success
    assert "reflection failure" in result.error_message
    assert rows(target, "t") == [(99,)]


def test_insertion_failure_restores_entire_previous_target(database):
    ddl = [
        "CREATE TABLE p(id INTEGER PRIMARY KEY)",
        "CREATE TABLE c(id INTEGER PRIMARY KEY,pid INTEGER REFERENCES p(id))",
    ]
    source = database(*ddl, "INSERT INTO p VALUES(1),(2)", "INSERT INTO c VALUES(1,2)")
    target = database(*ddl, "INSERT INTO p VALUES(99)", "INSERT INTO c VALUES(99,99)")
    result = materialize(source, target, {"p": [1], "c": [1]})
    assert not result.success
    assert rows(target, "p") == [(99,)]
    assert rows(target, "c") == [(99, 99)]
    assert result.total_rows_inserted == 0


def test_creation_failure_preserves_existing_data_and_cleans_new_tables(database):
    source = database(
        "CREATE TABLE a(id INTEGER PRIMARY KEY)",
        "CREATE TABLE b(id INTEGER PRIMARY KEY)",
        "CREATE TABLE c(id INTEGER PRIMARY KEY)",
    )
    target = database("CREATE TABLE a(id INTEGER PRIMARY KEY)", "INSERT INTO a VALUES(99)")

    @sa.event.listens_for(target, "before_cursor_execute")
    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        if "CREATE TABLE c" in statement:
            raise RuntimeError("injected CREATE failure")

    result = materialize(source, target, {})
    assert not result.success
    assert rows(target, "a") == [(99,)]
    assert sa.inspect(target).get_table_names() == ["a"]


def test_successful_refresh_uses_no_ddl_on_existing_tables(database):
    source = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(1)")
    target = database("CREATE TABLE t(id INTEGER PRIMARY KEY)", "INSERT INTO t VALUES(99)")
    statements = []

    @sa.event.listens_for(target, "before_cursor_execute")
    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip().upper())

    result = materialize(source, target, {"t": [1]})
    assert result.success, result.error_message
    assert rows(target, "t") == [(1,)]
    assert not any(s.startswith(("DROP", "CREATE", "TRUNCATE", "ALTER")) for s in statements)
