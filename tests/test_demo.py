"""Presentation controls must preserve CLI settings and recover from failures."""

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from typer.testing import CliRunner

import safeslicesql.demo as demo
from safeslicesql.cli import app
from safeslicesql.config import AppConfig, DatabaseConfig


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    workload = tmp_path / "workloads/world_workload.sql"
    workload.parent.mkdir()
    workload.write_text("SELECT * FROM country", encoding="utf-8")
    config = AppConfig(
        DatabaseConfig("localhost", 3306, "reader", "secret", "world"),
        DatabaseConfig("localhost", 3306, "writer", "secret", "world_test_benchmark"),
    )
    monkeypatch.setattr(demo, "load_config", lambda **kwargs: config)
    execute = Mock(return_value=True)
    monkeypatch.setattr(demo, "_execute", execute)
    return CliRunner(), execute


def test_menu_starts_and_exits_without_database_access(menu):
    runner, execute = menu
    result = runner.invoke(app, ["demo"], input="0\n")
    assert result.exit_code == 0, result.output
    assert "world_test_benchmark" in result.output
    assert "Live Demo" in result.output
    assert "secret" not in result.output
    execute.assert_not_called()


def test_inspection_uses_selected_source(menu):
    runner, execute = menu
    result = runner.invoke(app, ["demo"], input="1\n0\n")
    assert result.exit_code == 0, result.output
    assert execute.call_args.args[0] == ["inspect", "--database", "world"]


def test_changed_controls_reach_real_run_arguments(menu):
    runner, execute = menu
    result = runner.invoke(app, ["demo"], input="2\n1\n20\n123\n\n\n\n4\n0\n")
    assert result.exit_code == 0, result.output
    arguments, settings = execute.call_args.args
    assert settings.method == "random-repair"
    assert arguments[arguments.index("--budget-percent") + 1] == "20"
    assert arguments[arguments.index("--seed") + 1] == "123"
    assert "--dry-run" not in arguments


def test_invalid_budget_is_reprompted(menu):
    runner, execute = menu
    result = runner.invoke(app, ["demo"], input="2\n3\n200\n0\n25\n42\n\n\n\n3\n0\n")
    assert result.exit_code == 0, result.output
    assert execute.call_args.args[1].budget == 25
    assert "--dry-run" in execute.call_args.args[0]


def test_comparison_uses_current_controls_and_unique_report_directory(menu):
    runner, execute = menu
    result = runner.invoke(app, ["demo"], input="5\n5\n0\n")
    assert result.exit_code == 0, result.output
    paths = [Path(call.args[0][2]) for call in execute.call_args_list]
    assert paths[0] != paths[1]
    config = yaml.safe_load(paths[0].read_text(encoding="utf-8"))
    assert config["matrix"] == {
        "methods": list(demo.METHODS),
        "budget_percents": [10],
        "seeds": [42],
    }
    assert Path(config["workload"]["path"]).is_absolute()
    assert "secret" not in paths[0].read_text(encoding="utf-8")


def test_failed_action_returns_to_menu(menu):
    runner, execute = menu
    execute.return_value = False
    result = runner.invoke(app, ["demo"], input="4\n1\n0\n")
    assert result.exit_code == 0, result.output
    assert execute.call_count == 2
    assert execute.call_args.args[0][0] == "inspect"


def test_missing_workload_never_starts_generation(menu):
    runner, execute = menu
    Path("workloads/world_workload.sql").unlink()
    result = runner.invoke(app, ["demo"], input="4\n0\n")
    assert result.exit_code == 0, result.output
    assert "Workload file does not exist" in result.output
    execute.assert_not_called()


def test_result_and_chart_options_before_run(menu):
    runner, execute = menu
    result = runner.invoke(app, ["demo"], input="6\n7\n0\n")
    assert result.exit_code == 0, result.output
    assert "Run a subset or comparison first" in result.output
    assert "generate comparison charts first" in result.output
    execute.assert_not_called()


def test_eof_exits_cleanly(menu):
    runner, _ = menu
    result = runner.invoke(app, ["demo"], input="")
    assert result.exit_code == 0, result.output


def test_generated_database_action_uses_defaults_and_can_show_report(menu):
    runner, execute = menu
    result = runner.invoke(app, ["demo"], input="8\n\n\n\n0\n")
    assert result.exit_code == 0, result.output
    arguments = execute.call_args.args[0]
    assert arguments[0] == "selftest"
    assert "--mysql" not in arguments
    assert arguments[arguments.index("--cases") + 1] == "5"
    assert arguments[arguments.index("--rows") + 1] == "100"


def test_subprocess_arguments_are_not_shell_interpreted(monkeypatch):
    settings = demo.DemoSettings("world", "target", "file with spaces.sql")
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(demo.subprocess, "run", run)
    assert demo._execute(["run", "--workload", settings.workload], settings)
    args, kwargs = run.call_args
    assert "file with spaces.sql" in args[0]
    assert kwargs.get("shell", False) is False
    assert kwargs["env"]["SAFESLICE_SOURCE_DATABASE"] == "world"
    assert kwargs["env"]["SAFESLICE_TARGET_DATABASE"] == "target"


def test_process_failure_is_reported(monkeypatch):
    monkeypatch.setattr(demo.subprocess, "run", Mock(return_value=Mock(returncode=1)))
    assert not demo._execute(["inspect"], demo.DemoSettings("world", "target", "workload.sql"))


def test_comparison_runs_existing_pipeline(tmp_path, monkeypatch):
    import sqlalchemy as sa

    from safeslicesql.benchmark import run_benchmark

    monkeypatch.chdir(tmp_path)
    source, target = sa.create_engine("sqlite://"), sa.create_engine("sqlite://")
    try:
        with source.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE t(id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO t VALUES(1),(2)")
        workload = tmp_path / "queries.sql"
        workload.write_text("SELECT * FROM t", encoding="utf-8")
        settings = demo.DemoSettings("source", "target", str(workload), budget=100)
        output = tmp_path / "comparison"
        result = run_benchmark(demo._comparison_config(settings, output), source, target)
        assert result.successful_runs == 3
        assert result.failed_runs == 0
        assert len(list((output / "plots").glob("*.png"))) == 4
        demo._show_results(output)
    finally:
        source.dispose()
        target.dispose()
