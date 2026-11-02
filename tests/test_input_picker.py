"""Selection controls avoid typed paths and leave cancelled actions untouched."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from typer.testing import CliRunner

import safeslicesql.demo as demo
import safeslicesql.input_picker as picker
from safeslicesql.cli import app


@pytest.mark.parametrize(
    "kind,method",
    [("csv", "askopenfilename"), ("xlsx", "askopenfilename"), ("folder", "askdirectory")],
)
def test_native_picker_returns_selected_path_and_closes_hidden_window(monkeypatch, kind, method):
    tk = pytest.importorskip("tkinter")
    from tkinter import filedialog

    root = Mock()
    monkeypatch.setattr(tk, "Tk", lambda: root)
    select = Mock(return_value="C:/Faculty Data/input.csv")
    monkeypatch.setattr(filedialog, method, select)
    assert picker.pick_input(kind) == Path("C:/Faculty Data/input.csv")
    assert select.call_args.kwargs["parent"] is root
    root.withdraw.assert_called_once()
    root.destroy.assert_called_once()


def test_cancelled_native_dialog_returns_no_path(monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tkinter import filedialog

    root = Mock()
    monkeypatch.setattr(tk, "Tk", lambda: root)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: "")
    assert picker.pick_input("csv") is None
    root.destroy.assert_called_once()


def test_database_list_filters_system_schemas_and_closes_engine(monkeypatch):
    engine = MagicMock()
    monkeypatch.setattr(picker.sa, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(
        picker.sa,
        "inspect",
        lambda _: SimpleNamespace(
            get_schema_names=lambda: [
                "world",
                "mysql",
                "College",
                "sys",
                "information_schema",
                "performance_schema",
            ]
        ),
    )
    config = SimpleNamespace(source_db=SimpleNamespace(url=Mock()))
    assert picker.available_databases(config) == ["College", "world"]
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("inputs", ["9\n1\n0\n0\n", "9\n2\n1\n0\n", "9\n3\n0\n"])
def test_cancelled_menu_selection_never_prepares_or_changes_dataset(monkeypatch, inputs):
    import safeslicesql.dataset_input as dataset_input

    config = SimpleNamespace(
        source_db=SimpleNamespace(database="world"), target_db=SimpleNamespace(database="target")
    )
    monkeypatch.setattr(demo, "load_config", lambda **kwargs: config)
    monkeypatch.setattr(picker, "available_databases", lambda _: ["faculty_db"])
    monkeypatch.setattr(picker, "pick_input", lambda _: None)
    prepare = Mock()
    monkeypatch.setattr(dataset_input, "prepare_dataset", prepare)
    result = CliRunner().invoke(app, ["demo"], input=inputs)
    assert result.exit_code == 0, result.output
    prepare.assert_not_called()
    assert "Source: world" in result.output
