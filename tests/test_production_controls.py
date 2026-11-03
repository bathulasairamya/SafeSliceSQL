"""Production controls fail closed without publishing incomplete copies."""

import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

import safeslicesql.jobs as jobs
from safeslicesql.acceptance import AcceptancePolicy
from safeslicesql.cli import app
from safeslicesql.dataset_input import import_files
from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.result_store import temporary_result_store
from safeslicesql.runtime import (
    LimitExceeded,
    Limits,
    OperationCancelled,
    bounded_engine,
    checkpoint,
    execution_context,
)
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.security import credential, remember_secret, safe_error
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.types import QueryResult, SelectionResult, WorkloadReport


def report(survived=False):
    return WorkloadReport(
        supported_queries=1,
        survival_denominator=1,
        query_survival_rate=float(survived),
        overall_result_retention=float(survived),
        per_query_results=[QueryResult("Q1", "SELECT * FROM t", survived=survived)],
    )


@pytest.mark.parametrize(
    "policy",
    [
        AcceptancePolicy(1),
        AcceptancePolicy(min_result_retention=0.5),
        AcceptancePolicy(required_queries=("Q1",)),
        AcceptancePolicy(required_queries=("missing",)),
    ],
)
def test_quality_gate_rejects_lost_or_unknown_required_queries(policy):
    assert not policy.evaluate(report()).passed


def test_quality_gate_accepts_explicit_requirements():
    assert AcceptancePolicy(1, 1, ("Q1",)).evaluate(report(True)).passed


@pytest.mark.parametrize("value", [-1, 2, float("nan"), True])
def test_invalid_acceptance_thresholds_are_rejected(value):
    with pytest.raises(ValueError):
        AcceptancePolicy(value)


def test_cancelled_copy_rolls_back_existing_target_rows():
    cancellation = threading.Event()
    with execution_context(Limits(), cancellation):
        source, target = bounded_engine("sqlite://"), bounded_engine("sqlite://")
        try:
            for engine, value in ((source, 1), (target, 99)):
                with engine.begin() as connection:
                    connection.exec_driver_sql("CREATE TABLE t(id INTEGER PRIMARY KEY)")
                    connection.exec_driver_sql(f"INSERT INTO t VALUES({value})")
            schema = inspect_schema(source)

            @sa.event.listens_for(target, "after_cursor_execute")
            def cancel_on_insert(conn, cursor, statement, parameters, context, many):
                if statement.startswith("INSERT"):
                    cancellation.set()

            with pytest.raises(OperationCancelled):
                materialize_subset(
                    source,
                    target,
                    schema,
                    SelectionResult("test", 42, 1, {"t": [1]}),
                    build_dependency_graph(schema),
                    "target",
                )
            cancellation.clear()
            with target.connect() as connection:
                assert connection.exec_driver_sql("SELECT * FROM t").all() == [(99,)]
        finally:
            source.dispose()
            target.dispose()


def test_sqlite_statement_timeout_interrupts_real_work():
    with (
        pytest.raises(LimitExceeded),
        execution_context(Limits(statement_seconds=1, job_seconds=10)),
    ):
        engine = bounded_engine("sqlite://")
        try:
            try:
                with engine.connect() as connection:
                    connection.exec_driver_sql(
                        "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<100000000) SELECT sum(n) FROM x"
                    ).scalar_one()
            except sa.exc.DBAPIError:
                checkpoint()
        finally:
            engine.dispose()


def test_job_deadline_and_result_store_quota():
    with execution_context(Limits(job_seconds=1)) as guard:
        guard.started -= 2
        with pytest.raises(LimitExceeded, match="time limit"):
            checkpoint()
        guard.started = time.monotonic()
    with execution_context(Limits(max_temp_bytes=8192)):
        with temporary_result_store() as store:
            with pytest.raises(sqlite3.DatabaseError):
                store.add_batch([(0, "x" * 100000, 1, 0)])


def test_input_row_limit_is_enforced(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text("A\n1\n2\n3\n", encoding="utf-8")
    engine = sa.create_engine("sqlite://")
    try:
        with execution_context(Limits(max_source_rows=2)):
            with pytest.raises(LimitExceeded, match="max_source_rows"):
                import_files(engine, path)
    finally:
        engine.dispose()


def test_owner_check_and_abandoned_job_recovery(tmp_path, monkeypatch):
    job = jobs.Job(tmp_path / "job")
    job.record["pid"] = 99999999
    jobs.atomic_json(job.path, job.record)
    assert jobs.recover_jobs(tmp_path) == [job.path]
    assert jobs.read_job(job.path)["state"] == "interrupted"
    job.record["owner"] = "someone_else"
    jobs.atomic_json(job.path, job.record)
    with pytest.raises(PermissionError):
        jobs.read_job(job.path)


@pytest.mark.parametrize(
    "name,server", [("world", "server"), ("safeslice_output_" + "a" * 16, "other-server")]
)
def test_retention_never_drops_nonowned_or_wrong_server_database(
    tmp_path, monkeypatch, name, server
):
    job = jobs.Job(tmp_path / "job")
    job.record.update(
        state="failed",
        updated_at=time.time() - 9 * 86400,
        schemas=[dict(name=name, server_uuid=server, state="created")],
    )
    jobs.atomic_json(job.path, job.record)
    admin = MagicMock()
    admin.connect.return_value.__enter__.return_value.exec_driver_sql.return_value.scalar_one.return_value = "server"
    import safeslicesql.runtime as runtime

    monkeypatch.setattr(runtime, "bounded_engine", lambda _: admin)
    cfg = SimpleNamespace(target_db=SimpleNamespace(url=sa.URL.create("mysql+pymysql")))
    with pytest.raises(ValueError, match="ownership"):
        jobs.cleanup_expired(tmp_path, cfg)
    admin.begin.assert_not_called()


def test_memory_budget_checks_actual_process_rss():
    with pytest.raises(LimitExceeded, match="memory budget"):
        with execution_context(Limits(max_memory_bytes=1)):
            pytest.fail("A one-byte process budget must not admit work.")


def test_credential_vault_refuses_plaintext_backend(monkeypatch):
    keyring = pytest.importorskip("keyring")

    from safeslicesql.security import os_vault

    monkeypatch.setattr(keyring, "get_keyring", lambda: SimpleNamespace(priority=1))
    with pytest.raises(ValueError, match="plaintext"):
        os_vault()


def test_cancel_command_is_confined_to_owned_job(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    job = jobs.Job(tmp_path / "results/job")
    result = CliRunner().invoke(app, ["jobs", "cancel", "--folder", str(job.folder)])
    assert result.exit_code == 0, result.output
    assert job.cancel_requested()
    outside = CliRunner().invoke(app, ["jobs", "cancel", "--folder", str(tmp_path / "outside")])
    assert outside.exit_code != 0


def test_secret_file_and_vault_references(tmp_path, monkeypatch):
    for suffix in ("", "_FILE", "_KEYRING_SERVICE"):
        monkeypatch.delenv("SAFESLICE_SOURCE_PASSWORD" + suffix, raising=False)
    path = tmp_path / "secret"
    path.write_text("private-value\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("SAFESLICE_SOURCE_PASSWORD_FILE", str(path))
    assert credential("source", "host", 3306, "user") == "private-value"
    monkeypatch.delenv("SAFESLICE_SOURCE_PASSWORD_FILE")
    monkeypatch.setenv("SAFESLICE_SOURCE_PASSWORD_KEYRING_SERVICE", "test-vault")
    import safeslicesql.security as security

    getter = Mock(return_value="vault-value")
    monkeypatch.setattr(security, "os_vault", lambda: SimpleNamespace(get_password=getter))
    assert credential("source", "host", 3306, "user") == "vault-value"
    getter.assert_called_once_with("test-vault", "user@host:3306")


def test_database_errors_never_expose_bound_values_or_passwords():
    remember_secret("private-password")
    assert "private-password" not in safe_error(ValueError("private-password"))
    original = Exception(1062, "sensitive-row")
    error = sa.exc.DBAPIError("INSERT sensitive SQL", {"value": "sensitive-row"}, original)
    public = safe_error(error)
    assert "1062" in public
    assert "sensitive" not in public
    assert "unavailable" in safe_error(sa.exc.DBAPIError("secret SQL", {}, Exception()))


def test_history_ignores_corrupt_job_records(tmp_path):
    job = jobs.Job(tmp_path / "job")
    job.record.pop("pid")
    jobs.atomic_json(job.path, job.record)
    assert list(jobs.owned_jobs(tmp_path)) == []
    assert jobs.recover_jobs(tmp_path) == []


def test_sqlite_source_connections_are_read_only(tmp_path):
    path = tmp_path / "source.db"
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(path)))
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    engine.dispose()
    engine = bounded_engine(sa.URL.create("sqlite", database=str(path)), read_only=True)
    try:
        with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO t VALUES(1)")
    finally:
        engine.dispose()


def test_shared_target_cannot_be_used_by_two_copy_jobs(tmp_path):
    from safeslicesql.target_lock import target_lease

    engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "target.db")))
    try:
        with target_lease(engine):
            with pytest.raises(ValueError, match="busy"):
                with target_lease(engine, timeout=0.1):
                    pass
        with target_lease(engine, timeout=0.1):
            pass
    finally:
        engine.dispose()


def test_scale_evidence_records_actual_rows_and_resources(tmp_path):
    from safeslicesql.scale_check import run_scale_check

    evidence = run_scale_check(tmp_path / "scale", levels=[5], seeds=[42], cases=1)
    assert evidence["production_certification"] is False
    assert evidence["state"] == "completed"
    sample = evidence["measurements"][0]
    assert sample["checks"] == 6 and sample["failed"] == 0
    assert sample["seconds"] > 0
    assert sample["peak_process_rss_bytes"] > 0
    assert sample["actual_source_rows_by_case"][1] > 0


def test_scale_evidence_preserves_completed_samples_when_cancelled(tmp_path, monkeypatch):
    import json

    import safeslicesql.scale_check as scale

    def suite(directory, *, rows, **kwargs):
        if rows == 10:
            raise OperationCancelled("Requested stop")
        return [{"success": True, "case": 1, "source_rows": 5}]

    monkeypatch.setattr(scale, "run_synthetic_suite", suite)
    output = tmp_path / "scale"
    with pytest.raises(OperationCancelled):
        scale.run_scale_check(output, levels=[5, 10], seeds=[42], cases=1)
    evidence = json.loads((output / "evidence.json").read_text())
    assert evidence["state"] == "stopped"
    assert len(evidence["measurements"]) == 1
    assert evidence["incomplete_sample"]["max_rows_per_table"] == 10


def test_rejected_quality_is_saved_in_job_journal(tmp_path, monkeypatch):
    import safeslicesql.desktop_service as service

    monkeypatch.setattr(
        service,
        "_create_copy",
        lambda *args: SimpleNamespace(
            results=SimpleNamespace(
                failed_runs=1,
                runs=[
                    SimpleNamespace(
                        acceptance=AcceptancePolicy(1).evaluate(report()),
                        error_message="Required coverage was lost",
                    )
                ],
            )
        ),
    )
    service.create_copy(Mock(), service.CopyRequest("csv", "input.csv"), tmp_path / "job")
    assert jobs.read_job(tmp_path / "job/job.json")["state"] == "rejected"
