import sqlalchemy as sa

from safeslicesql.copy_evidence import inspect_copy
from safeslicesql.result_views import evidence_text, show_charts


def databases():
    engines = [sa.create_engine("sqlite://") for _ in range(2)]
    for engine in engines:
        with engine.begin() as c:
            c.exec_driver_sql("CREATE TABLE department(id INTEGER PRIMARY KEY, name TEXT)")
            c.exec_driver_sql(
                "CREATE TABLE student(id INTEGER PRIMARY KEY, department_id INTEGER REFERENCES department(id), name TEXT)"
            )
            c.exec_driver_sql("INSERT INTO department VALUES(1, 'Computing')")
            c.exec_driver_sql("INSERT INTO student VALUES(10,1,'Ananya'),(20,NULL,'Sam')")
    return engines


def test_evidence_checks_records_and_exposes_related_record():
    source, target = databases()
    try:
        report = inspect_copy(source, target)
        assert report["passed"]
        student = next(t for t in report["tables"] if t["table"] == "student")
        assert student["checked_rows"] == 2
        assert student["relationships"][0]["checked"] == 1
        assert student["relationships"][0]["examples"][0]["parent_in_copy"]["name"] == "Computing"
        assert "Ananya" in evidence_text(student)
        with target.begin() as c:
            c.exec_driver_sql("UPDATE student SET name='Altered' WHERE id=10")
            c.exec_driver_sql("INSERT INTO student VALUES(30,999,'Missing original')")
        report = inspect_copy(source, target)
        assert not report["passed"]
        student = next(t for t in report["tables"] if t["table"] == "student")
        assert student["changed_records"] == 1
        assert student["missing_originals"] == 1
        assert student["relationships"][0]["missing_parents"] == 1
    finally:
        source.dispose()
        target.dispose()


def test_empty_relationship_is_explicit_and_missing_table_fails():
    source, target = databases()
    try:
        with target.begin() as c:
            c.exec_driver_sql("DELETE FROM student")
        student = next(t for t in inspect_copy(source, target)["tables"] if t["table"] == "student")
        assert "EMPTY COPY TABLE" in evidence_text(student)
        assert student["relationships"][0]["checked"] == 0
        with target.begin() as c:
            c.exec_driver_sql("DROP TABLE student")
        report = inspect_copy(source, target)
        assert not report["passed"]
        assert report["missing_tables"] == ["student"]
    finally:
        source.dispose()
        target.dispose()


def test_chart_button_explains_empty_chart_folder(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from safeslicesql import result_views

    (tmp_path / "plots").mkdir()
    notice = Mock()
    monkeypatch.setattr(result_views.messagebox, "showinfo", notice)
    show_charts(None, tmp_path)
    assert "no generated charts" in notice.call_args.args[1]


def test_composite_keys_and_changes_beyond_example_rows_are_checked():
    engines = [sa.create_engine("sqlite://") for _ in range(2)]
    try:
        for engine in engines:
            with engine.begin() as c:
                c.exec_driver_sql(
                    "CREATE TABLE pair(a INTEGER, b INTEGER, value TEXT, PRIMARY KEY(a,b))"
                )
                c.execute(
                    sa.text("INSERT INTO pair VALUES(:a,:b,:v)"),
                    [{"a": i, "b": 1, "v": "same"} for i in range(401)],
                )
        with engines[1].begin() as c:
            c.exec_driver_sql("UPDATE pair SET value='changed' WHERE a=400")
        report = inspect_copy(*engines)
        assert not report["passed"]
        assert report["tables"][0]["checked_rows"] == 401
        assert report["tables"][0]["changed_records"] == 1
        assert len(report["tables"][0]["examples"]) == 3
    finally:
        for engine in engines:
            engine.dispose()


def test_chart_action_uses_viewer_not_folder_launcher(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from safeslicesql import desktop

    viewer = Mock()
    monkeypatch.setattr(desktop, "show_charts", viewer)
    ui = desktop.Desktop.__new__(desktop.Desktop)
    ui.root = Mock()
    ui.outcome = SimpleNamespace(folder=tmp_path)
    ui._open("charts")
    viewer.assert_called_once_with(ui.root, tmp_path)
