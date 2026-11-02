"""Faculty data must flow through the real pipeline without sample-specific SQL."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
import yaml
from typer.testing import CliRunner

import safeslicesql.demo as demo
from safeslicesql.automatic_workload import generate_workload, save_automatic_workload
from safeslicesql.benchmark import run_benchmark
from safeslicesql.cli import app
from safeslicesql.config import DatabaseConfig
from safeslicesql.dataset_input import import_files
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.synthetic import SHAPES, generate_database
from safeslicesql.workload_analyzer import analyze_workload


@pytest.fixture
def engine():
    engine = sa.create_engine("sqlite://")
    yield engine
    engine.dispose()


@pytest.mark.parametrize("shape", SHAPES)
def test_auto_queries_execute_on_unfamiliar_schema(engine, tmp_path, shape):
    generate_database(engine, seed=123, rows=10, shape=shape)
    path = save_automatic_workload(inspect_schema(engine), tmp_path)
    analysis = analyze_workload(path, engine)
    assert analysis.queries
    assert all(
        query.is_supported and query.source_evaluated and not query.analysis_error
        for query in analysis.queries
    )


def test_csv_preserves_strings_and_duplicate_headers(engine, tmp_path):
    path = tmp_path / "Faculty Data.csv"
    path.write_text(
        'ID,Name,Name,_safeslice_id\n001,"Doe, Jane",A,original\n002,"O\'Reilly",B,other\n',
        encoding="utf-8-sig",
    )
    report = import_files(engine, path)[0]
    assert report["rows"] == 2
    table = sa.Table(report["table"], sa.MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        values = connection.execute(sa.select(table).order_by(table.c._safeslice_id)).all()
    assert values[0] == (1, "001", "Doe, Jane", "A", "original")
    assert values[1] == (2, "002", "O'Reilly", "B", "other")
    assert len({column["stored"] for column in report["columns"]}) == 4


def test_semicolon_csv_and_multiple_files(engine, tmp_path):
    (tmp_path / "First.csv").write_text("Code;Value\nA;100\n", encoding="utf-8")
    (tmp_path / "Second.CSV").write_text("Key,Amount\n1,20\n", encoding="utf-8")
    reports = import_files(engine, tmp_path)
    assert len(reports) == 2
    assert all(report["rows"] == 1 for report in reports)


def test_irregular_csv_is_rejected(engine, tmp_path):
    path = tmp_path / "broken.csv"
    path.write_text("A,B\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected 2"):
        import_files(engine, path)


def test_existing_tables_cannot_be_overwritten(engine, tmp_path):
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE important(id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO important VALUES(1)")
    with pytest.raises(ValueError, match="new empty source"):
        import_files(engine, tmp_path / "input.csv")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT * FROM important").all() == [(1,)]


def test_excel_sheets_and_formulas_are_text(engine, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "Students"
    first.append(["Code", "Score"])
    first.append(["001", 95])
    second = workbook.create_sheet("Formula")
    second.append(["Expression"])
    second.append(["=1+2"])
    path = tmp_path / "Faculty.xlsx"
    workbook.save(path)
    workbook.close()
    reports = import_files(engine, path)
    assert len(reports) == 2
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            f"SELECT `Code`,`Score` FROM `{reports[0]['table']}`"
        ).one() == ("001", "95")
        assert (
            connection.exec_driver_sql(
                f"SELECT `Expression` FROM `{reports[1]['table']}`"
            ).scalar_one()
            == "=1+2"
        )


def test_auto_benchmark_copies_unknown_csv_schema(engine, tmp_path):
    path = tmp_path / "Unseen.csv"
    path.write_text("Registration,Course\n001,Physics\n002,Chemistry\n", encoding="utf-8")
    import_files(engine, path)
    target = sa.create_engine("sqlite://")
    config = tmp_path / "benchmark.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "matrix": {"methods": list(demo.METHODS), "budget_percents": [100], "seeds": [42]},
                "workload": {"path": "auto"},
                "output": {
                    key: str(tmp_path / key) for key in ("raw_dir", "summary_dir", "plots_dir")
                },
            }
        ),
        encoding="utf-8",
    )
    try:
        result = run_benchmark(config, engine, target)
        assert result.successful_runs == 3
        assert all(run.workload.overall_result_retention == 1 for run in result.runs)
        assert len(list((tmp_path / "raw_dir/workloads").glob("*.sql"))) == 1
    finally:
        target.dispose()


def test_missing_primary_keys_are_diagnosed(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE raw_data(name TEXT)")
    with pytest.raises(ValueError, match="primary key"):
        generate_workload(inspect_schema(engine))


def test_menu_loads_faculty_database_and_replaces_settings(tmp_path, monkeypatch):
    import safeslicesql.dataset_input as dataset_input
    import safeslicesql.input_picker as input_picker

    source = DatabaseConfig("source-host", 3307, "reader", "secret", "faculty_db")
    prepared = SimpleNamespace(
        source=source,
        target=replace(source, database="fresh_target"),
        workload=tmp_path / "workload.sql",
        tables={"new_table": 7},
        manifest=tmp_path / "dataset.json",
    )
    monkeypatch.setattr(demo, "load_config", Mock())
    monkeypatch.setattr(input_picker, "available_databases", lambda _: ["faculty_db"])
    prepare = Mock(return_value=prepared)
    monkeypatch.setattr(dataset_input, "prepare_dataset", prepare)
    execute = Mock(return_value=True)
    monkeypatch.setattr(demo, "_execute", execute)
    result = CliRunner().invoke(app, ["demo"], input="9\n1\n1\n1\n0\n")
    assert result.exit_code == 0, result.output
    assert prepare.call_args.kwargs["database"] == "faculty_db"
    assert execute.call_args.args[0] == ["inspect", "--database", "faculty_db"]
    assert "secret" not in result.output


def test_file_source_connection_is_passed_privately_to_child(monkeypatch):
    source = DatabaseConfig("import-host", 3307, "import-user", "private-password", "imported")
    settings = demo.DemoSettings("imported", "target", "auto", source_connection=source)
    process = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(demo.subprocess, "run", process)
    assert demo._execute(["inspect", "--database", "imported"], settings)
    environment = process.call_args.kwargs["env"]
    assert environment["SAFESLICE_SOURCE_HOST"] == "import-host"
    assert environment["SAFESLICE_SOURCE_PASSWORD"] == "private-password"
    assert "private-password" not in " ".join(process.call_args.args[0])
