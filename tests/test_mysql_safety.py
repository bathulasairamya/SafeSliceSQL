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


def test_mysql_readonly_source_and_statement_deadline(mysql_pair):
    import time

    from safeslicesql.runtime import Limits, bounded_engine, execution_context

    source, _ = mysql_pair
    execute(source, "CREATE TABLE t(id INT PRIMARY KEY) ENGINE=InnoDB")
    execute(source, "INSERT INTO t VALUES(1)")
    with execution_context(Limits(statement_seconds=1, job_seconds=10)):
        reader = bounded_engine(source.url, read_only=True)
        try:
            with pytest.raises(sa.exc.DBAPIError), reader.begin() as connection:
                connection.exec_driver_sql("INSERT INTO t VALUES(2)")
            started = time.monotonic()
            with pytest.raises(sa.exc.DBAPIError), reader.connect() as connection:
                connection.exec_driver_sql("SELECT id FROM t WHERE SLEEP(3)").all()
            assert time.monotonic() - started < 3
        finally:
            reader.dispose()


def test_mysql_cancel_before_commit_restores_previous_copy(mysql_pair):
    import threading

    from safeslicesql.runtime import (
        OperationCancelled,
        bounded_engine,
        execution_context,
    )

    source, target = mysql_pair
    execute(source, "CREATE TABLE t(id INT PRIMARY KEY) ENGINE=InnoDB", "INSERT INTO t VALUES(1)")
    execute(target, "CREATE TABLE t(id INT PRIMARY KEY) ENGINE=InnoDB", "INSERT INTO t VALUES(99)")
    cancellation = threading.Event()
    with execution_context(cancellation=cancellation):
        guarded = bounded_engine(target.url)
        try:

            @sa.event.listens_for(guarded, "after_cursor_execute")
            def cancel_on_insert(conn, cursor, statement, parameters, context, many):
                if statement.startswith("INSERT"):
                    cancellation.set()

            schema = inspect_schema(source)
            with pytest.raises(OperationCancelled):
                materialize_subset(
                    source,
                    guarded,
                    schema,
                    SelectionResult("test", 42, 1, {"t": [1]}),
                    build_dependency_graph(schema),
                    target.url.database,
                )
            cancellation.clear()
            with target.connect() as connection:
                assert connection.exec_driver_sql("SELECT * FROM t").all() == [(99,)]
        finally:
            guarded.dispose()


def test_mysql_target_lease_rejects_overlapping_copy(mysql_pair):
    from safeslicesql.target_lock import target_lease

    _, target = mysql_pair
    with target_lease(target):
        with pytest.raises(ValueError, match="busy"):
            with target_lease(target, timeout=0):
                pytest.fail("A second connection must not acquire this target.")
    with target_lease(target, timeout=0):
        pass


def test_mysql_guarded_copy_journal_and_owned_retention(mysql_pair, tmp_path):
    import time

    from safeslicesql.config import AppConfig, DatabaseConfig
    from safeslicesql.desktop_service import CopyRequest, create_copy
    from safeslicesql.jobs import atomic_json, cleanup_expired, read_job

    source, target = mysql_pair
    url = source.url
    config = DatabaseConfig(url.host, url.port, url.username, url.password or "", url.database)
    cfg = AppConfig(config, config)
    path = tmp_path / "input.csv"
    path.write_text("Code,Name\n001,First\n002,Second\n", encoding="utf-8")
    outcome = create_copy(cfg, CopyRequest("csv", str(path), budget=100), tmp_path / "job")
    record = read_job(outcome.folder / "job.json")
    try:
        assert record["state"] == "completed"
        assert len(record["schemas"]) == 2
        assert all(schema["state"] == "created" for schema in record["schemas"])
        assert outcome.results.successful_runs == 1
        record["updated_at"] = time.time() - 9 * 86400
        atomic_json(outcome.folder / "job.json", record)
        removed = cleanup_expired(tmp_path, cfg, days=7)
        assert set(removed) == {outcome.prepared.source.database, outcome.prepared.target.database}
        with target.connect() as connection:
            names = set(connection.exec_driver_sql("SHOW DATABASES").scalars())
        assert source.url.database in names and target.url.database in names
    finally:
        with target.begin() as connection:
            for name in (outcome.prepared.target.database, outcome.prepared.source.database):
                connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote_ident(name)}")


def test_mysql_prefix_writer_has_no_access_to_existing_source(mysql_pair, tmp_path):
    from safeslicesql.config import AppConfig, DatabaseConfig
    from safeslicesql.dataset_input import prepare_dataset

    source, target = mysql_pair
    url = source.url
    user = "ss_writer_" + uuid.uuid4().hex[:12]
    password = uuid.uuid4().hex
    execute(source, "CREATE TABLE original(id INT PRIMARY KEY) ENGINE=InnoDB")
    with target.begin() as connection:
        if connection.exec_driver_sql("SELECT @@partial_revokes").scalar_one():
            pytest.skip("Prefix-grant profile requires partial_revokes=OFF")
        connection.execute(
            sa.text(f"CREATE USER '{user}'@'%' IDENTIFIED BY :password"),
            {"password": password},
        )
        for prefix in (r"safeslice\_input\_%", r"safeslice\_output\_%"):
            connection.execution_options(no_parameters=True).exec_driver_sql(
                f"GRANT SELECT,INSERT,DELETE,CREATE,DROP,REFERENCES,INDEX ON `{prefix}`.* TO '{user}'@'%'"
            )
    writer = sa.create_engine(url.set(username=user, password=password, database=None))
    prepared = None
    try:
        with pytest.raises(sa.exc.DBAPIError), writer.connect() as connection:
            connection.exec_driver_sql(f"SELECT * FROM {quote_ident(url.database)}.original").all()
        config = DatabaseConfig(url.host, url.port, user, password, "")
        file = tmp_path / "input.csv"
        file.write_text("A\n1\n", encoding="utf-8")
        prepared = prepare_dataset(AppConfig(config, config), tmp_path / "prepared", files=file)
        assert prepared.tables
    finally:
        writer.dispose()
        with target.begin() as connection:
            if prepared:
                for name in (prepared.source.database, prepared.target.database):
                    connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote_ident(name)}")
            connection.execute(sa.text(f"DROP USER '{user}'@'%'")).close()


@pytest.mark.parametrize("kind", ["existing", "csv", "xlsx"])
def test_faculty_input_preparation_and_full_pipeline(mysql_pair, tmp_path, kind):
    import yaml

    from safeslicesql.benchmark import run_benchmark
    from safeslicesql.config import AppConfig, DatabaseConfig
    from safeslicesql.dataset_input import prepare_dataset
    from safeslicesql.source_snapshot import capture_source_snapshot
    from safeslicesql.synthetic import generate_database

    source, target = mysql_pair
    url = source.url
    source_config = DatabaseConfig(
        url.host, url.port, url.username, url.password or "", url.database
    )
    config = AppConfig(
        source_config,
        DatabaseConfig(url.host, url.port, url.username, url.password or "", target.url.database),
    )
    if kind == "existing":
        generate_database(source, seed=77, rows=10, shape="branch")
        before = capture_source_snapshot(source)
        prepared = prepare_dataset(config, tmp_path / "prepared", database=source.url.database)
    else:
        file = tmp_path / f"faculty.{kind}"
        if kind == "csv":
            file.write_text('Code,Name\n001,"First, Student"\n002,Second\n', encoding="utf-8")
        else:
            openpyxl = pytest.importorskip("openpyxl")
            workbook = openpyxl.Workbook()
            workbook.active.append(["Code", "Name"])
            workbook.active.append(["001", "First, Student"])
            workbook.active.append(["002", "Second"])
            workbook.save(file)
            workbook.close()
        prepared = prepare_dataset(config, tmp_path / "prepared", files=file)
    engines = [sa.create_engine(prepared.source.url), sa.create_engine(prepared.target.url)]
    try:
        path = tmp_path / "benchmark.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "matrix": {
                        "methods": ["random-repair", "referential", "workload-aware"],
                        "budget_percents": [100],
                        "seeds": [42],
                    },
                    "workload": {"path": str(prepared.workload)},
                    "output": {
                        key: str(tmp_path / key) for key in ("raw_dir", "summary_dir", "plots_dir")
                    },
                }
            ),
            encoding="utf-8",
        )
        result = run_benchmark(path, *engines, prepared.target.database)
        assert result.successful_runs == 3
        assert all(run.workload.overall_result_retention == 1 for run in result.runs)
        assert "root" not in prepared.manifest.read_text()
        if kind == "existing":
            assert capture_source_snapshot(source) == before
        else:
            table = next(iter(prepared.tables))
            with engines[0].connect() as connection:
                assert connection.exec_driver_sql(
                    f"SELECT `Code` FROM {quote_ident(table)} ORDER BY `_safeslice_id`"
                ).scalars().all() == ["001", "002"]
    finally:
        for engine in engines:
            engine.dispose()
        names = [prepared.target.database]
        if kind != "existing":
            names.append(prepared.source.database)
        with target.begin() as connection:
            for name in names:
                connection.exec_driver_sql(f"DROP DATABASE {quote_ident(name)}")


def test_failed_faculty_import_cleans_up_only_its_owned_schema(mysql_pair, tmp_path):
    from safeslicesql.config import AppConfig, DatabaseConfig
    from safeslicesql.dataset_input import prepare_dataset

    source, target = mysql_pair
    url = source.url
    cfg = DatabaseConfig(url.host, url.port, url.username, url.password or "", url.database)
    config = AppConfig(cfg, cfg)
    with target.connect() as connection:
        before = set(connection.exec_driver_sql("SHOW DATABASES").scalars())
    path = tmp_path / "bad.csv"
    path.write_text("A,B\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected 2"):
        prepare_dataset(config, tmp_path / "prepared", files=path)
    with target.connect() as connection:
        assert set(connection.exec_driver_sql("SHOW DATABASES").scalars()) == before


def test_mysql_generated_schema_suite(mysql_pair, tmp_path, monkeypatch):
    from types import SimpleNamespace

    import safeslicesql.synthetic as synthetic

    source, _ = mysql_pair
    monkeypatch.setattr(
        synthetic,
        "load_config",
        lambda **kwargs: SimpleNamespace(target_db=SimpleNamespace(url=source.url)),
    )
    with source.connect() as connection:
        before = set(
            connection.execute(
                sa.text(
                    "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME LIKE :prefix"
                ),
                {"prefix": "safeslice_check_%"},
            ).scalars()
        )
    records = synthetic.run_synthetic_suite(tmp_path / "mysql_checks", cases=5, rows=10, mysql=True)
    assert len(records) == 30
    assert all(record["success"] for record in records), records
    with source.connect() as connection:
        after = set(
            connection.execute(
                sa.text(
                    "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME LIKE :prefix"
                ),
                {"prefix": "safeslice_check_%"},
            ).scalars()
        )
    assert before == after


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
