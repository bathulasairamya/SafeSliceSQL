"""Generated schemas provide independent cross-database pipeline evidence."""

import json
from unittest.mock import MagicMock, Mock

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

import safeslicesql.synthetic as synthetic
from safeslicesql.cli import app
from safeslicesql.source_snapshot import capture_source_snapshot


@pytest.mark.parametrize("shape", synthetic.SHAPES)
def test_generator_is_reproducible_and_has_valid_references(shape):
    engines = [sa.create_engine("sqlite://"), sa.create_engine("sqlite://")]
    try:
        generated = [
            synthetic.generate_database(engine, seed=42, rows=20, shape=shape) for engine in engines
        ]
        assert generated[0] == generated[1]
        assert capture_source_snapshot(engines[0]) == capture_source_snapshot(engines[1])
        with engines[0].connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        tables = generated[0][1]["tables"]
        assert 3 <= len(tables) <= 6
        assert all("node_" in table for table in tables)
        assert all(count <= 20 for count in tables.values())
    finally:
        for engine in engines:
            engine.dispose()


def test_suite_exercises_all_shapes_methods_and_full_copy(tmp_path):
    output = tmp_path / "checks"
    records = synthetic.run_synthetic_suite(output, cases=5, rows=20)
    assert len(records) == 30
    assert all(record["success"] for record in records), records
    assert {record["shape"] for record in records} == set(synthetic.SHAPES)
    assert all(record["reproducible"] and record["source_unchanged"] for record in records)
    assert all(
        record["result_retention"] == 1 for record in records if record["budget_percent"] == 100
    )
    assert len(json.loads((output / "results.json").read_text())) == 30
    assert (output / "results.csv").is_file()
    assert len(list(output.glob("case_*/manifest.json"))) == 5


def test_suite_does_not_reuse_existing_output_directory(tmp_path):
    with pytest.raises(FileExistsError):
        synthetic.run_synthetic_suite(tmp_path)


def test_mysql_cleanup_never_adopts_preexisting_schemas(tmp_path, monkeypatch):
    admin = MagicMock()
    connection = admin.begin.return_value.__enter__.return_value
    connection.exec_driver_sql.side_effect = RuntimeError("database already exists")
    monkeypatch.setattr(synthetic, "load_config", Mock())
    monkeypatch.setattr(synthetic.sa, "create_engine", Mock(return_value=admin))
    with pytest.raises(RuntimeError, match="already exists"):
        with synthetic.synthetic_pair(tmp_path, mysql=True):
            pass
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert len(statements) == 1
    assert statements[0].startswith("CREATE DATABASE")
    assert "IF NOT EXISTS" not in statements[0]
    assert not any("DROP" in statement for statement in statements)
    admin.dispose.assert_called_once()


def test_failure_is_reported_and_cli_returns_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        synthetic, "generate_database", Mock(side_effect=RuntimeError("injected failure"))
    )
    result = CliRunner().invoke(app, ["selftest", "--cases", "1"])
    assert result.exit_code == 1, result.output
    assert "0 passed, 1 failed" in result.output
    report = next((tmp_path / "results/selftest").glob("*/results.json"))
    assert "injected failure" in report.read_text()


def test_suite_records_failure_and_continues_other_methods(tmp_path, monkeypatch):
    real_select = synthetic.select_rows

    def select(**options):
        if options["method"] == "referential":
            raise ValueError("selection failed")
        return real_select(**options)

    monkeypatch.setattr(synthetic, "select_rows", select)
    records = synthetic.run_synthetic_suite(tmp_path / "checks", cases=1, rows=10)
    assert len(records) == 6
    assert sum(record["success"] for record in records) == 4
    assert all(record["success"] for record in records if record["method"] != "referential")


@pytest.mark.parametrize("arguments", [["--cases", "0"], ["--rows", "0"], ["--cases", "101"]])
def test_cli_rejects_invalid_scale(arguments):
    result = CliRunner().invoke(app, ["selftest", *arguments])
    assert result.exit_code == 2
