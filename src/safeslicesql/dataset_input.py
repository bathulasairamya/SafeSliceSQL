"""Load faculty files into owned schemas and prepare unfamiliar MySQL sources."""

from __future__ import annotations

import contextlib
import csv
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT

from safeslicesql.automatic_workload import save_automatic_workload
from safeslicesql.config import AppConfig, DatabaseConfig
from safeslicesql.db import quote_ident
from safeslicesql.jobs import intend_schema, register_schema, removed_schema
from safeslicesql.runtime import bounded_engine, cap, suspend_limits
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.security import safe_error


def _columns(headers):
    names, mapping = [], []
    used = {"_safeslice_id"}
    for index, header in enumerate(headers, 1):
        original = "" if header is None else str(header)
        base = original.strip()[:50] or f"column_{index}"
        name, suffix = base, 1
        while name.casefold() in used:
            suffix += 1
            name = f"{base}_{suffix}"
        used.add(name.casefold())
        names.append(name)
        mapping.append({"original": original, "stored": name})
    if not names:
        raise ValueError("Input table has no header columns.")
    return names, mapping


def _load_table(engine, label, number, headers, rows, prior_rows=0):
    names, mapping = _columns(headers)
    cap(len(names) + 1, "max_columns")  # Include the generated primary key.
    cap(number, "max_tables")
    stem = re.sub(r"[^a-zA-Z0-9_]", "_", label).strip("_")[:40] or "table"
    table_name = f"data_{number}_{stem}"
    metadata = sa.MetaData()
    table = sa.Table(
        table_name,
        metadata,
        sa.Column("_safeslice_id", sa.Integer, primary_key=True, autoincrement=False),
        *[
            sa.Column(sa.sql.quoted_name(name, True), sa.Text().with_variant(LONGTEXT(), "mysql"))
            for name in names
        ],
        mysql_engine="InnoDB",
    )
    table.create(engine)
    count = 0
    with engine.begin() as connection:
        batch = []
        for index, values in enumerate(rows, 2):
            values = list(values)
            if not values:  # Ignore completely empty CSV lines.
                continue
            if len(values) != len(names):
                raise ValueError(
                    f"{label}: row {index} has {len(values)} cells; expected {len(names)}."
                )
            count += 1
            cap(prior_rows + count, "max_source_rows")
            for value in values:
                if value is not None:
                    cap(len(str(value).encode("utf-8")), "max_cell_bytes")
            row = {
                name: None if value is None else str(value) for name, value in zip(names, values)
            }
            row["_safeslice_id"] = count
            batch.append(row)
            if len(batch) >= 500:
                connection.execute(table.insert(), batch)
                batch.clear()
        if batch:
            connection.execute(table.insert(), batch)
    return {
        "input": label,
        "table": table_name,
        "rows": count,
        "columns": mapping,
        "added_primary_key": "_safeslice_id",
    }


def iter_input_tables(path: Path):
    """Read tables without importing; handles stay open only while consuming each table."""
    files = (
        sorted(file for file in path.iterdir() if file.is_file() and file.suffix.lower() == ".csv")
        if path.is_dir()
        else [path]
    )
    if not files:
        raise ValueError("The folder contains no .csv files.")
    if any(not file.is_file() or file.suffix.lower() not in {".csv", ".xlsx"} for file in files):
        raise ValueError("Choose a .csv file, a folder of CSV files, or an .xlsx workbook.")
    cap(sum(file.stat().st_size for file in files), "max_input_bytes")
    found = False
    for file in files:
        if file.suffix.lower() == ".csv":
            with file.open(encoding="utf-8-sig", newline="") as handle:
                sample = handle.read(8192)
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                except csv.Error:
                    dialect = csv.excel
                reader = csv.reader(handle, dialect=dialect, strict=True)
                headers = next(reader, None)
                if headers is None:
                    raise ValueError(f"{file.name} is empty; a header row is required.")
                found = True
                yield file.stem, headers, reader

        else:
            try:
                from openpyxl import load_workbook
            except ImportError as exc:
                raise ValueError(
                    "Excel support requires: pip install 'safeslicesql[excel]'"
                ) from exc
            from zipfile import ZipFile

            with ZipFile(file) as archive:
                cap(sum(info.file_size for info in archive.infolist()), "max_input_bytes")
            workbook = load_workbook(file, read_only=True, data_only=False)
            try:
                for sheet in workbook.worksheets:
                    iterator = sheet.iter_rows(values_only=True)
                    headers = next(iterator, None)
                    if headers is None or all(value is None for value in headers):
                        continue
                    found = True
                    yield sheet.title, headers, iterator

            finally:
                workbook.close()
    if not found:
        raise ValueError("No nonempty worksheet headers were found.")


def import_files(engine: sa.Engine, path: Path) -> list[dict]:
    """Stream files into a fresh database using the same reader as the input preview."""
    if sa.inspect(engine).get_table_names():
        raise ValueError("File import requires a new empty source database.")
    reports = []
    with contextlib.closing(iter_input_tables(path)) as tables:
        for label, headers, rows in tables:
            # Preserve the historical CSV table stem and manifest label.
            reports.append(
                _load_table(
                    engine,
                    label,
                    len(reports) + 1,
                    headers,
                    rows,
                    sum(report["rows"] for report in reports),
                )
            )
    return reports


@dataclass
class PreparedDataset:
    source: DatabaseConfig
    target: DatabaseConfig
    workload: Path
    tables: dict[str, int]
    manifest: Path


def prepare_dataset(
    config: AppConfig, output: Path, *, database: str | None = None, files: Path | None = None
) -> PreparedDataset:
    """Existing DBs stay read-only. File sources and targets get fresh UUID names.

    Successful imported sources/targets remain available for the live demo.
    Failure cleans up only databases created by this operation.
    """
    if (database is None) == (files is None):
        raise ValueError("Choose either an existing MySQL database or input files.")
    output.mkdir(parents=True, exist_ok=False)
    token = uuid4().hex[:16]
    source_config = (
        replace(config.source_db, database=database)
        if database
        else replace(config.target_db, database=f"safeslice_input_{token}")
    )
    target_config = replace(config.target_db, database=f"safeslice_output_{token}")
    errors = AppConfig(source_config, target_config).validate()
    if errors:
        raise ValueError("; ".join(errors))
    created, engines = [], []
    admin = None
    try:
        if files is not None:
            if not files.exists():
                raise ValueError("Input file/folder does not exist.")
            admin = bounded_engine(config.target_db.url.set(database=None))
            intend_schema(source_config.database)
            with admin.begin() as connection:
                connection.exec_driver_sql(f"CREATE DATABASE {quote_ident(source_config.database)}")
            created.append(source_config.database)
            register_schema(admin, source_config.database, "imported_source")
        source = bounded_engine(source_config.url, read_only=files is None)
        engines.append(source)
        mappings = import_files(source, files) if files is not None else []
        schema = inspect_schema(source)
        # Generate and validate the schema-dependent queries before creating a target.
        workload = save_automatic_workload(schema, output)
        if admin is None:
            admin = bounded_engine(config.target_db.url.set(database=None))
        intend_schema(target_config.database)
        with admin.begin() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote_ident(target_config.database)}")
        created.append(target_config.database)
        register_schema(admin, target_config.database, "target")
        manifest = output / "dataset.json"
        manifest.write_text(
            json.dumps(
                {
                    "source": source_config.database,
                    "target": target_config.database,
                    "input_kind": "files" if files is not None else "existing_mysql",
                    "tables": {name: table.row_count for name, table in schema.tables.items()},
                    "file_mappings": mappings,
                    "workload": str(workload.resolve()),
                    "query_purpose": "Structural demonstration; no business workload inferred.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return PreparedDataset(
            source_config,
            target_config,
            workload,
            {name: table.row_count for name, table in schema.tables.items()},
            manifest,
        )
    except BaseException:
        for engine in engines:
            engine.dispose()
        cleanup_errors = []
        for name in reversed(created):
            try:
                with suspend_limits(), admin.begin() as connection:
                    connection.exec_driver_sql(f"DROP DATABASE {quote_ident(name)}")
                removed_schema(name)
            except Exception as exc:
                cleanup_errors.append(
                    f"Cleanup failed for owned database {name}: {safe_error(exc)}"
                )
        if cleanup_errors:
            raise RuntimeError("; ".join(cleanup_errors))
        raise
    finally:
        for engine in engines:
            engine.dispose()
        if admin is not None:
            admin.dispose()
