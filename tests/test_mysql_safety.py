"""Optional MySQL 8 integration tests against an explicitly configured test server.

Set SAFESLICE_TEST_MYSQL_URL to a disposable server account with CREATE/DROP
DATABASE privileges. Each test owns two UUID-named schemas and removes only those.
"""

import os
import uuid

import pytest
import sqlalchemy as sa

from safeslicesql.db import assert_distinct_databases, quote_ident
from safeslicesql.dependency_graph import build_dependency_graph, compute_closure
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.types import SelectionResult
from safeslicesql.validator import validate_subset
from safeslicesql.workload_analyzer import analyze_workload

pytestmark = pytest.mark.db


@pytest.fixture
def mysql_pair():
    url = os.environ.get("SAFESLICE_TEST_MYSQL_URL")
    if not url:
        pytest.skip("SAFESLICE_TEST_MYSQL_URL is not configured")
    admin = sa.create_engine(url)
    names = [f"safeslice_test_{uuid.uuid4().hex}" for _ in range(2)]
    created = []
    engines = []
    try:
        with admin.begin() as connection:
            for name in names:
                connection.exec_driver_sql(f"CREATE DATABASE {quote_ident(name)}")
                created.append(name)
                engines.append(sa.create_engine(admin.url.set(database=name)))
        yield engines
    finally:
        for engine in engines:
            engine.dispose()
        with admin.begin() as connection:
            for name in created:
                connection.exec_driver_sql(f"DROP DATABASE {quote_ident(name)}")
        admin.dispose()


def execute(engine, *statements):
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def materialize(source, target, selected):
    schema = inspect_schema(source)
    return materialize_subset(
        source,
        target,
        schema,
        SelectionResult("test", 42, 20, selected),
        build_dependency_graph(schema),
        target.url.database,
    )


def test_mysql_identity_uses_server_not_hostname(mysql_pair):
    source, target = mysql_pair
    alias = sa.create_engine(source.url.set(host="localhost"))
    try:
        with pytest.raises(ValueError, match="identical"):
            assert_distinct_databases(source, alias, target.url.database)
        with pytest.raises(ValueError, match="does not match"):
            assert_distinct_databases(source, target, "misleading_name")
        assert_distinct_databases(source, target, target.url.database)
    finally:
        alias.dispose()


def test_mysql_rejected_ddl_is_never_executed(mysql_pair, tmp_path):
    source, _ = mysql_pair
    execute(source, "CREATE TABLE t(id INT PRIMARY KEY) ENGINE=InnoDB", "INSERT INTO t VALUES(1)")
    workload = tmp_path / "workload.sql"
    workload.write_text("DROP TABLE t; DELETE FROM t;", encoding="utf-8")
    analysis = analyze_workload(workload, source)
    assert not analysis.supported_queries
    with source.connect() as connection:
        assert connection.exec_driver_sql("SELECT id FROM t").scalar_one() == 1


def test_mysql_failed_refresh_rolls_back_previous_rows(mysql_pair):
    source, target = mysql_pair
    ddl = [
        "CREATE TABLE p(id INT PRIMARY KEY) ENGINE=InnoDB",
        "CREATE TABLE c(id INT PRIMARY KEY,pid INT,FOREIGN KEY(pid) REFERENCES p(id)) ENGINE=InnoDB",
    ]
    execute(source, *ddl, "INSERT INTO p VALUES(1),(2)", "INSERT INTO c VALUES(1,2)")
    execute(target, *ddl, "INSERT INTO p VALUES(99)", "INSERT INTO c VALUES(99,99)")
    result = materialize(source, target, {"p": [1], "c": [1]})
    assert not result.success
    assert "transaction failed" in result.error_message
    with target.connect() as connection:
        assert connection.exec_driver_sql("SELECT id FROM p").fetchall() == [(99,)]
        assert connection.exec_driver_sql("SELECT id,pid FROM c").fetchall() == [(99, 99)]
    result = materialize(source, target, {"p": [2], "c": [1]})
    assert result.success, result.error_message
    assert validate_subset(source, target, 20).is_valid


def test_mysql_exact_schema_and_non_pk_reference(mysql_pair):
    source, target = mysql_pair
    execute(
        source,
        "CREATE TABLE p(id INT PRIMARY KEY,code VARCHAR(10) UNIQUE,amount DECIMAL(18,4),happened DATETIME,payload BLOB,description VARCHAR(1000)) ENGINE=InnoDB",
        "CREATE TABLE c(id INT PRIMARY KEY,pcode VARCHAR(10),FOREIGN KEY(pcode) REFERENCES p(code)) ENGINE=InnoDB",
        "INSERT INTO p VALUES(7,'ABC',123.4567,'2024-01-01 12:34:56',X'0102','original')",
        "INSERT INTO c VALUES(2,'abc')",
    )
    schema = inspect_schema(source)
    closure = compute_closure(build_dependency_graph(schema), {"c": {2}}, source)
    assert closure.selected_rows["p"] == {7}
    result = materialize(
        source, target, {table: list(keys) for table, keys in closure.selected_rows.items()}
    )
    assert result.success, result.error_message
    assert validate_subset(source, target, 20).is_valid
    with source.connect() as src, target.connect() as tgt:
        assert (
            src.exec_driver_sql("SELECT * FROM p").fetchall()
            == tgt.exec_driver_sql("SELECT * FROM p").fetchall()
        )


def test_mysql_nontransactional_tables_are_rejected(mysql_pair):
    source, target = mysql_pair
    ddl = "CREATE TABLE t(id INT PRIMARY KEY) ENGINE=MyISAM"
    execute(source, ddl, "INSERT INTO t VALUES(1)")
    execute(target, ddl, "INSERT INTO t VALUES(99)")
    result = materialize(source, target, {"t": [1]})
    assert not result.success
    assert "InnoDB" in result.error_message
    with target.connect() as connection:
        assert connection.exec_driver_sql("SELECT id FROM t").fetchall() == [(99,)]


def test_mysql_missing_constraints_fail_validation(mysql_pair):
    source, target = mysql_pair
    execute(
        source,
        "CREATE TABLE p(id INT PRIMARY KEY) ENGINE=InnoDB",
        "CREATE TABLE c(id INT PRIMARY KEY,pid INT,FOREIGN KEY(pid) REFERENCES p(id)) ENGINE=InnoDB",
    )
    execute(
        target,
        "CREATE TABLE p(id INT PRIMARY KEY) ENGINE=InnoDB",
        "CREATE TABLE c(id INT,pid INT) ENGINE=InnoDB",
        "INSERT INTO c VALUES(1,999),(1,999)",
    )
    report = validate_subset(source, target, 10)
    assert not report.is_valid
    assert not report.schema_compatible
    assert not report.pk_unique
    assert report.fk_violations == 2


@pytest.mark.parametrize("chunk_size", [1, 500])
def test_mysql_self_reference_order_and_refresh(mysql_pair, chunk_size):
    source, target = mysql_pair
    ddl = "CREATE TABLE t(id INT PRIMARY KEY,parent INT,FOREIGN KEY(parent) REFERENCES t(id)) ENGINE=InnoDB"
    execute(source, ddl, "INSERT INTO t VALUES(30,NULL),(20,30),(1,20)")
    execute(target, ddl, "INSERT INTO t VALUES(99,NULL),(10,99)")
    schema = inspect_schema(source)
    result = materialize_subset(
        source,
        target,
        schema,
        SelectionResult("test", 42, 3, {"t": [1, 20, 30]}),
        build_dependency_graph(schema),
        target.url.database,
        chunk_size,
    )
    assert result.success, result.error_message
    assert validate_subset(source, target, 3).is_valid
    result = materialize(source, target, {"t": [30]})
    assert result.success, result.error_message
    with target.connect() as connection:
        assert connection.exec_driver_sql("SELECT * FROM t").fetchall() == [(30, None)]


@pytest.mark.parametrize("storage", ["STORED", "VIRTUAL"])
def test_mysql_generated_columns_are_not_inserted(mysql_pair, storage):
    source, target = mysql_pair
    execute(
        source,
        f"CREATE TABLE t(id INT PRIMARY KEY,value INT,doubled INT GENERATED ALWAYS AS (value*2) {storage}) ENGINE=InnoDB",
        "INSERT INTO t(id,value) VALUES(1,3),(2,5)",
    )
    result = materialize(source, target, {"t": [2, 1]})
    assert result.success, result.error_message
    assert validate_subset(source, target, 20).is_valid
    with target.connect() as connection:
        assert connection.exec_driver_sql("SELECT * FROM t ORDER BY id").fetchall() == [
            (1, 3, 6),
            (2, 5, 10),
        ]


def test_mysql_missing_row_rolls_back_and_reports_zero(mysql_pair):
    source, target = mysql_pair
    ddl = "CREATE TABLE t(id INT PRIMARY KEY) ENGINE=InnoDB"
    execute(source, ddl, "INSERT INTO t VALUES(1)")
    execute(target, ddl, "INSERT INTO t VALUES(99)")
    schema = inspect_schema(source)
    result = materialize_subset(
        source,
        target,
        schema,
        SelectionResult("test", 42, 2, {"t": [1, 2]}),
        build_dependency_graph(schema),
        target.url.database,
        1,
    )
    assert not result.success
    assert "missing" in result.error_message
    assert result.total_rows_inserted == 0
    assert result.rows_inserted == {}
    with target.connect() as connection:
        assert connection.exec_driver_sql("SELECT id FROM t").fetchall() == [(99,)]


def test_mysql_workload_literals_comments_and_quoted_aggregate(mysql_pair, tmp_path):
    from safeslicesql.sampler import select_rows

    source, _ = mysql_pair
    execute(
        source,
        "CREATE TABLE `odd table`(id INT PRIMARY KEY,value TEXT) ENGINE=InnoDB",
        "INSERT INTO `odd table` VALUES(1,'a;b--c#d/*e*/'),(2,'other')",
    )
    path = tmp_path / "workload.sql"
    path.write_text(
        "SELECT * FROM `odd table` -- comment;\nWHERE value='a;b--c#d/*e*/'; "
        "SELECT COUNT(*) FROM `odd table`;",
        encoding="utf-8",
    )
    workload = analyze_workload(path, source)
    assert len(workload.supported_queries) == 2
    assert [query.source_row_count for query in workload.queries] == [1, 1]
    assert workload.queries[1].tables == ["odd table"]
    selection = select_rows(
        inspect_schema(source), workload, engine=source, budget=1, method="workload-aware"
    )
    assert selection.total_selected == 1


def test_mysql_content_snapshot_detects_update_without_count_change(mysql_pair):
    from safeslicesql.source_snapshot import capture_source_snapshot

    source, target = mysql_pair
    ddl = "CREATE TABLE t(id INT PRIMARY KEY,value VARCHAR(30),amount DECIMAL(18,4)) ENGINE=InnoDB"
    execute(source, ddl, "INSERT INTO t VALUES(1,'original',12.3456)")
    execute(target, ddl, "INSERT INTO t VALUES(1,'original',12.3456)")
    baseline = capture_source_snapshot(source)
    assert (
        validate_subset(source, target, 10, initial_source_snapshot=baseline).source_unchanged
        is True
    )
    execute(source, "UPDATE t SET amount=98.7654 WHERE id=1")
    report = validate_subset(source, target, 10, initial_source_snapshot=baseline)
    assert report.source_unchanged is False
    assert not report.is_valid


def test_mysql_duplicate_results_and_group_alias_retention(mysql_pair, tmp_path):
    from safeslicesql.validator import evaluate_workload

    source, target = mysql_pair
    ddl = "CREATE TABLE t(id INT PRIMARY KEY,value VARCHAR(10)) ENGINE=InnoDB"
    execute(source, ddl, "INSERT INTO t VALUES(1,'A'),(2,'A'),(3,'B')")
    execute(target, ddl, "INSERT INTO t VALUES(1,'A'),(2,'A')")
    path = tmp_path / "metrics.sql"
    path.write_text(
        "SELECT value FROM t; SELECT value AS label,COUNT(*) AS n FROM t GROUP BY value;",
        encoding="utf-8",
    )
    report = evaluate_workload(source, target, analyze_workload(path, source))
    assert report.failed_queries == 0
    assert report.per_query_results[0].result_row_retention == pytest.approx(2 / 3)
    assert report.per_query_results[1].aggregate_retention == 0.5


def test_mysql_duplicate_column_names_are_not_overwritten(mysql_pair, tmp_path):
    from safeslicesql.validator import evaluate_workload

    source, target = mysql_pair
    ddl = [
        "CREATE TABLE a(id INT PRIMARY KEY) ENGINE=InnoDB",
        "CREATE TABLE b(id INT PRIMARY KEY) ENGINE=InnoDB",
    ]
    execute(source, *ddl, "INSERT INTO a VALUES(1)", "INSERT INTO b VALUES(9)")
    execute(target, *ddl, "INSERT INTO a VALUES(2)", "INSERT INTO b VALUES(9)")
    path = tmp_path / "metrics.sql"
    path.write_text("SELECT a.id,b.id FROM a JOIN b ON 1=1", encoding="utf-8")
    report = evaluate_workload(source, target, analyze_workload(path, source))
    assert report.failed_queries == 0
    assert report.overall_result_retention == report.overall_key_retention == 0


def test_mysql_errors_and_empty_queries_have_honest_denominators(mysql_pair, tmp_path):
    from safeslicesql.validator import evaluate_workload

    source, target = mysql_pair
    ddl = "CREATE TABLE t(id INT PRIMARY KEY) ENGINE=InnoDB"
    execute(
        source,
        ddl,
        "INSERT INTO t VALUES(1)",
        "CREATE TABLE extra(id INT PRIMARY KEY)",
        "INSERT INTO extra VALUES(1)",
    )
    execute(target, ddl, "INSERT INTO t VALUES(1)")
    path = tmp_path / "metrics.sql"
    path.write_text(
        "SELECT * FROM t; SELECT * FROM t WHERE id=-1; SELECT * FROM extra; SELECT * FROM t WHERE id IN (SELECT id FROM t);",
        encoding="utf-8",
    )
    report = evaluate_workload(source, target, analyze_workload(path, source))
    assert report.excluded_queries == report.failed_queries == 1
    assert report.supported_queries == 3
    assert report.survival_denominator == 2
    assert report.query_survival_rate == 0.5
