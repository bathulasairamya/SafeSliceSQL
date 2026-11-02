"""Reproducible cross-schema checks using databases owned by this test run."""

from __future__ import annotations

import csv
import json
import random
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

from safeslicesql.config import load_config
from safeslicesql.db import quote_ident
from safeslicesql.dependency_graph import build_dependency_graph
from safeslicesql.sampler import METHODS, select_rows
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.source_snapshot import capture_source_snapshot
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.validator import evaluate_workload, validate_subset
from safeslicesql.workload_analyzer import analyze_workload

SHAPES = ("chain", "star", "branch", "self-reference", "independent")


@contextmanager
def synthetic_pair(directory: Path, *, mysql: bool = False):
    """SQLite is local by default. MySQL creates/drops only newly owned schemas."""
    engines = []
    created = []
    admin = None
    try:
        if mysql:
            config = load_config(validate=False)
            admin = sa.create_engine(config.target_db.url.set(database="mysql"))
            # No IF NOT EXISTS: a collision must never adopt an existing database.
            for role in ("source", "target"):
                name = f"safeslice_check_{uuid4().hex}_{role}"
                with admin.begin() as connection:
                    connection.exec_driver_sql(f"CREATE DATABASE {quote_ident(name)}")
                created.append(name)
                engines.append(sa.create_engine(admin.url.set(database=name)))
        else:
            directory.mkdir(parents=True, exist_ok=True)
            for role in ("source", "target"):
                engine = sa.create_engine(
                    sa.URL.create("sqlite", database=str((directory / f"{role}.db").resolve()))
                )

                @sa.event.listens_for(engine, "connect")
                def foreign_keys(connection, record):
                    connection.execute("PRAGMA foreign_keys=ON")

                engines.append(engine)
        yield tuple(engines)
    finally:
        for engine in engines:
            engine.dispose()
        if admin is not None:
            cleanup_errors = []
            try:
                for name in reversed(created):
                    try:
                        with admin.begin() as connection:
                            connection.exec_driver_sql(f"DROP DATABASE {quote_ident(name)}")
                    except Exception as exc:
                        cleanup_errors.append(f"Could not remove owned test schema {name}: {exc}")
            finally:
                admin.dispose()
            if cleanup_errors:
                raise RuntimeError("; ".join(cleanup_errors))


def generate_database(engine: sa.Engine, *, seed: int, rows: int, shape: str) -> tuple[str, dict]:
    """Vary names, table counts, data skew, nulls, composite PKs, and FK topology."""
    if rows < 1 or shape not in SHAPES:
        raise ValueError("Positive row scale and a known schema shape are required.")
    rng = random.Random(seed)
    metadata = sa.MetaData()
    tables, counts, references = [], {}, {}
    for index in range(rng.randint(3, 6)):
        name = f"node_{rng.randrange(10000):04d}_{index}"
        columns = [sa.Column("id", sa.Integer, primary_key=True, autoincrement=False)]
        if index > 0 and index == 2:
            columns.append(sa.Column("part", sa.Integer, primary_key=True, autoincrement=False))
        columns += [
            sa.Column("category", sa.String(48), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        ]
        parents = []
        if index and shape != "independent":
            # Composite-key tables are leaves; all FKs remain single-column.
            available = [t for t in tables if len(t.primary_key.columns) == 1]
            if shape == "chain":
                parents = [available[-1]]
            elif shape in ("star", "self-reference"):
                parents = [tables[0]]
            else:
                parents = rng.sample(available, min(len(available), rng.randint(1, 2)))
        refs = []
        for parent in parents:
            column = f"parent_{len(refs)}"
            columns.append(
                sa.Column(column, sa.Integer, sa.ForeignKey(f"{parent.name}.id"), nullable=True)
            )
            refs.append((column, parent.name))
        if index == 0 and shape == "self-reference":
            columns.append(
                sa.Column("ancestor", sa.Integer, sa.ForeignKey(f"{name}.id"), nullable=True)
            )
            refs.append(("ancestor", name))
        table = sa.Table(name, metadata, *columns, mysql_engine="InnoDB")
        tables.append(table)
        references[name] = refs
        counts[name] = max(1, rows // 4) if index == 0 else rng.randint(max(1, rows // 2), rows)
        if shape == "independent" and index == 2:
            counts[name] = 0  # Empty-table and source-empty-query handling.
    metadata.create_all(engine)
    with engine.begin() as connection:
        for table in tables:
            batch = []
            for key in range(1, counts[table.name] + 1):
                row = {
                    "id": key,
                    "category": rng.choice(["A", "A", "B", "C", "O'Reilly"]),
                    "amount": Decimal(rng.randrange(100000)) / 100,
                }
                if "part" in table.c:
                    row["part"] = key % 3
                for column, parent in references[table.name]:
                    maximum = key - 1 if parent == table.name else counts[parent]
                    row[column] = (
                        None
                        if maximum == 0 or rng.random() < 0.2
                        else (1 if rng.random() < 0.7 else rng.randint(1, maximum))
                    )
                batch.append(row)
                if len(batch) >= 1000:
                    connection.execute(table.insert(), batch)
                    batch.clear()
            if batch:
                connection.execute(table.insert(), batch)
    queries = []
    for table in tables:
        quoted = quote_ident(table.name)
        queries.extend(
            [
                f"SELECT * FROM {quoted}",
                f"SELECT category AS label,COUNT(*) AS n FROM {quoted} GROUP BY category",
                f"SELECT id,amount FROM {quoted} WHERE amount>100",
            ]
        )
        for column, parent in references[table.name]:
            queries.append(
                f"SELECT a.id,b.id FROM {quoted} a JOIN {quote_ident(parent)} b ON a.{quote_ident(column)}=b.id"
            )
    return ";\n".join(queries) + ";\n", {
        "shape": shape,
        "seed": seed,
        "tables": counts,
        "references": references,
    }


def run_synthetic_suite(
    output: Path,
    *,
    cases: int = 5,
    rows: int = 100,
    seed: int = 42,
    mysql: bool = False,
    progress=None,
) -> list[dict]:
    """Exercise all methods at 10% and 100%, including replay and target refresh."""
    if cases < 1 or rows < 1:
        raise ValueError("Case count and row scale must be positive.")
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for index in range(cases):
        directory = output / f"case_{index + 1}"
        directory.mkdir()
        shape = SHAPES[index % len(SHAPES)]
        case_seed = seed + index
        try:
            with synthetic_pair(directory, mysql=mysql) as (source, target):
                sql, manifest = generate_database(source, seed=case_seed, rows=rows, shape=shape)
                (directory / "manifest.json").write_text(
                    json.dumps(manifest, indent=2), encoding="utf-8"
                )
                workload_path = directory / "workload.sql"
                workload_path.write_text(sql, encoding="utf-8")
                baseline = capture_source_snapshot(source)
                schema = inspect_schema(source)
                graph = build_dependency_graph(schema)
                workload = analyze_workload(workload_path, source)
                for budget_percent in (10, 100):
                    budget = max(1, schema.total_row_count * budget_percent // 100)
                    for method in METHODS:
                        started = time.perf_counter()
                        record = {
                            "case": index + 1,
                            "shape": shape,
                            "data_seed": case_seed,
                            "backend": source.dialect.name,
                            "source_rows": schema.total_row_count,
                            "method": method,
                            "budget_percent": budget_percent,
                            "budget_limit": budget,
                            "success": False,
                            "actual_rows": None,
                            "source_unchanged": None,
                            "reproducible": False,
                            "query_survival": None,
                            "result_retention": None,
                            "error": "",
                        }
                        try:
                            options = dict(
                                schema=schema,
                                workload=workload,
                                graph=graph,
                                engine=source,
                                budget=budget,
                                method=method,
                                seed=seed,
                            )
                            selected = select_rows(**options)
                            replay = select_rows(**options)
                            record["reproducible"] = selected.selected_rows == replay.selected_rows
                            generation = materialize_subset(
                                source,
                                target,
                                schema,
                                selected,
                                graph,
                                target.url.database or "synthetic_target",
                            )
                            evaluated = evaluate_workload(source, target, workload)
                            validation = validate_subset(
                                source, target, budget, initial_source_snapshot=baseline
                            )
                            record.update(
                                actual_rows=validation.actual_rows,
                                source_unchanged=validation.source_unchanged,
                                query_survival=evaluated.query_survival_rate,
                                result_retention=evaluated.overall_result_retention,
                            )
                            errors = [
                                generation.error_message,
                                *validation.errors,
                                *[q.error for q in evaluated.per_query_results if q.error],
                            ]
                            if not record["reproducible"]:
                                errors.append("Selection changed when replayed with the same seed.")
                            if budget_percent == 100 and (
                                validation.actual_rows != schema.total_row_count
                                or any(
                                    q.result_row_retention != 1
                                    for q in evaluated.per_query_results
                                    if not q.excluded
                                )
                            ):
                                errors.append(
                                    "Full-budget run did not reproduce every source result."
                                )
                            record["error"] = "; ".join(error for error in errors if error)
                            record["success"] = bool(
                                generation.success
                                and validation.is_valid
                                and record["reproducible"]
                                and not record["error"]
                            )
                        except Exception as exc:
                            record["error"] = str(exc)
                        record["seconds"] = round(time.perf_counter() - started, 4)
                        records.append(record)
                        if progress:
                            progress(record)
        except Exception as exc:
            records.append(
                {
                    "case": index + 1,
                    "shape": shape,
                    "success": False,
                    "error": f"Case setup/cleanup failed: {exc}",
                }
            )
        # Persist after every case so interrupted suites retain completed evidence.
        (output / "results.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        fields = list(dict.fromkeys(key for record in records for key in record))
        with (output / "results.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
    return records
