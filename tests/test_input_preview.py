"""Input counts must describe the same rows the copy pipeline will import."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy as sa

from safeslicesql.dataset_input import import_files
from safeslicesql.input_preview import InputPreview, preview_input
from safeslicesql.runtime import LimitExceeded, Limits, execution_context


@pytest.mark.parametrize("kind", ["csv", "folder", "xlsx"])
def test_file_preview_matches_import_without_database_access(tmp_path, monkeypatch, kind):
    import safeslicesql.input_preview as module

    monkeypatch.setattr(
        module, "bounded_engine", Mock(side_effect=AssertionError("Preview must not connect"))
    )
    if kind == "xlsx":
        from openpyxl import Workbook

        path = tmp_path / "example.xlsx"
        book = Workbook()
        book.active.append(["Code", "Name"])
        book.active.append(["001", "First"])
        book.active.append([None, None])
        book.active.append(["002", "Second"])
        book.create_sheet("empty")
        book.save(path)
        book.close()
    else:
        path = tmp_path / "example.csv"
        path.write_text('Code,Name\n001,"First\nStudent"\n\n002,Second\n', encoding="utf-8")
        if kind == "folder":
            (tmp_path / "other.csv").write_text("A\n1\n", encoding="utf-8")
            path = tmp_path
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    result = preview_input(None, kind, str(path))
    engine = sa.create_engine("sqlite://")
    try:
        imported = import_files(engine, path)
        assert result.total_rows == sum(table["rows"] for table in imported)
        assert len(result.tables) == len(imported)
        assert [t[2] for t in result.tables] == [len(t["columns"]) for t in imported]
    finally:
        engine.dispose()
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


def test_preview_enforces_row_limits_and_rejects_malformed_input(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text("A\n1\n2\n")
    with pytest.raises(LimitExceeded), execution_context(Limits(max_source_rows=1)):
        preview_input(None, "csv", str(path))
    path.write_text("A,B\n1,2,3\n")
    with pytest.raises(ValueError, match="cells"):
        preview_input(None, "csv", str(path))
    path.write_text("\n1\n")
    with pytest.raises(ValueError, match="header columns"):
        preview_input(None, "csv", str(path))


def test_mysql_preview_uses_only_source_with_readonly_engine(monkeypatch):
    import safeslicesql.input_preview as module
    from safeslicesql.config import DatabaseConfig

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE example(id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO example VALUES(1),(2)")
    factory = Mock(return_value=engine)
    monkeypatch.setattr(module, "bounded_engine", factory)
    config = SimpleNamespace(source_db=DatabaseConfig("localhost", 3306, "reader", "", "original"))
    result = preview_input(config, "mysql", "chosen")
    assert result.tables == [("example", 2, 1)]
    assert factory.call_args.args[0].database == "chosen"
    assert factory.call_args.kwargs == {"read_only": True}
    assert config.source_db.database == "original"


def test_budget_rounding_matches_copy_and_handles_empty_input():
    assert InputPreview([("world", 5302, 5)], []).row_budget(10) == 530
    assert InputPreview([("small", 2, 1)], []).row_budget(10) == 1
    assert InputPreview([], []).row_budget(10) == 0
