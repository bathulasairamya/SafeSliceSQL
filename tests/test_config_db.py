"""Regression tests for database configuration and engine construction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy.engine import URL
from typer.testing import CliRunner

import safeslicesql.cli as cli_module
import safeslicesql.config as config_module
import safeslicesql.db as db_module
from safeslicesql.config import AppConfig, DatabaseConfig, load_config
from safeslicesql.types import SchemaMetadata


ENV_KEYS = (
    "SAFESLICE_SOURCE_HOST",
    "SAFESLICE_SOURCE_PORT",
    "SAFESLICE_SOURCE_USER",
    "SAFESLICE_SOURCE_PASSWORD",
    "SAFESLICE_SOURCE_DATABASE",
    "SAFESLICE_TARGET_HOST",
    "SAFESLICE_TARGET_PORT",
    "SAFESLICE_TARGET_USER",
    "SAFESLICE_TARGET_PASSWORD",
    "SAFESLICE_TARGET_DATABASE",
)


def _clear_database_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_load_config_reads_source_and_target_credentials(tmp_path, monkeypatch):
    _clear_database_environment(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "SAFESLICE_SOURCE_HOST=source-host",
                "SAFESLICE_SOURCE_PORT=3307",
                "SAFESLICE_SOURCE_USER=source-user",
                "SAFESLICE_SOURCE_PASSWORD=source-secret",
                "SAFESLICE_SOURCE_DATABASE=employees",
                "SAFESLICE_TARGET_HOST=target-host",
                "SAFESLICE_TARGET_PORT=3308",
                "SAFESLICE_TARGET_USER=target-user",
                "SAFESLICE_TARGET_PASSWORD=target-secret",
                "SAFESLICE_TARGET_DATABASE=employees_slice",
            )
        ),
        encoding="utf-8",
    )

    config = load_config(env_file)

    assert config.source_db.host == "source-host"
    assert config.source_db.port == 3307
    assert config.source_db.user == "source-user"
    assert config.source_db.password == "source-secret"
    assert config.source_db.database == "employees"
    assert config.target_db.host == "target-host"
    assert config.target_db.port == 3308
    assert config.target_db.user == "target-user"
    assert config.target_db.password == "target-secret"
    assert config.target_db.database == "employees_slice"


def test_default_env_file_is_project_root_relative(tmp_path, monkeypatch):
    _clear_database_environment(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SAFESLICE_SOURCE_DATABASE=from_project_env\n"
        "SAFESLICE_SOURCE_USER=source-user\n"
        "SAFESLICE_TARGET_DATABASE=target_from_project_env\n"
        "SAFESLICE_TARGET_USER=target-user\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(Path.cwd().parent)
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    config = load_config()

    assert config.source_db.database == "from_project_env"
    assert config.target_db.database == "target_from_project_env"


def test_database_url_preserves_sensitive_password_characters():
    password = "p@ss:/?#[]% +"
    config = DatabaseConfig(
        host="localhost",
        port=3306,
        user="safe_user",
        password=password,
        database="employees",
    )

    assert isinstance(config.url, URL)
    assert config.url.password == password
    assert config.url.username == "safe_user"
    assert config.url.database == "employees"


def test_source_and_target_engines_receive_their_own_url_objects(monkeypatch):
    source = DatabaseConfig("source-host", 3307, "source-user", "source-pass", "employees")
    target = DatabaseConfig("target-host", 3308, "target-user", "target-pass", "employees_slice")
    config = AppConfig(source_db=source, target_db=target)
    engine_factory = Mock(return_value=object())
    monkeypatch.setattr(db_module.sa, "create_engine", engine_factory)

    db_module.get_source_engine(config)
    db_module.get_target_engine(config)

    source_url = engine_factory.call_args_list[0].args[0]
    target_url = engine_factory.call_args_list[1].args[0]
    assert isinstance(source_url, URL)
    assert isinstance(target_url, URL)
    assert (source_url.host, source_url.port, source_url.username, source_url.password, source_url.database) == (
        "source-host", 3307, "source-user", "source-pass", "employees"
    )
    assert (target_url.host, target_url.port, target_url.username, target_url.password, target_url.database) == (
        "target-host", 3308, "target-user", "target-pass", "employees_slice"
    )


def test_invalid_configuration_is_actionable_and_does_not_expose_password(tmp_path, monkeypatch):
    _clear_database_environment(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SAFESLICE_SOURCE_HOST=\n"
        "SAFESLICE_SOURCE_USER=\n"
        "SAFESLICE_SOURCE_PASSWORD=do-not-expose-this\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        load_config(env_file)

    message = str(exc_info.value)
    assert "Source database host is required" in message
    assert "Source database user is required" in message
    assert "do-not-expose-this" not in message


def test_invalid_port_raises_actionable_error_without_secrets(tmp_path, monkeypatch):
    _clear_database_environment(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SAFESLICE_SOURCE_PORT=not-a-port\n"
        "SAFESLICE_SOURCE_PASSWORD=do-not-expose-this\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="SAFESLICE_SOURCE_PORT must be an integer") as exc_info:
        load_config(env_file)

    assert "do-not-expose-this" not in str(exc_info.value)


def test_connection_failure_log_does_not_include_exception_text(caplog):
    engine = Mock()
    engine.url.database = "employees"
    engine.connect.side_effect = RuntimeError("do-not-expose-this")

    assert db_module.test_connection(engine) is False
    assert "do-not-expose-this" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_cli_inspect_passes_source_config_to_engine(monkeypatch):
    source = DatabaseConfig("localhost", 3306, "source-user", "source-pass", "configured_source")
    target = DatabaseConfig("localhost", 3306, "target-user", "target-pass", "configured_target")
    config = AppConfig(source_db=source, target_db=target)
    engine = object()
    graph = Mock(has_cycles=False, topological_order=Mock(return_value=[]))
    engine_factory = Mock(return_value=engine)
    monkeypatch.setattr(cli_module, "load_config", Mock(return_value=config))
    monkeypatch.setattr(cli_module, "create_engine", engine_factory)
    monkeypatch.setattr(cli_module, "test_connection", Mock(return_value=True))
    monkeypatch.setattr(cli_module, "inspect_schema", Mock(return_value=SchemaMetadata("employees")))
    monkeypatch.setattr(cli_module, "build_dependency_graph", Mock(return_value=graph))

    result = CliRunner().invoke(cli_module.app, ["inspect", "--database", "employees"])

    assert result.exit_code == 0, result.output
    assert engine_factory.call_args.args[0] is source
    assert source.database == "employees"
    assert target.database == "configured_target"