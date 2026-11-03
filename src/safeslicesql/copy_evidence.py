"""Read-only, inspectable evidence about the current source and copy contents."""

import json
from datetime import datetime, timezone

import sqlalchemy as sa

from safeslicesql.jobs import atomic_json
from safeslicesql.record_browser import copy_engines
from safeslicesql.runtime import cap, checkpoint
from safeslicesql.schema_compatibility import table_schema_differences


def inspect_copy(source, target):
    """Check every copied row; retain at most three illustrative records per table.

    This is a fresh inspection, not a claim that a live source stayed frozen since
    generation. The original pipeline's fingerprint check is reported separately.
    """
    si, ti = sa.inspect(source), sa.inspect(target)
    source_names, target_names = si.get_table_names(), ti.get_table_names()
    cap(len(source_names), "max_tables")
    report = {
        "inspected_at": datetime.now(timezone.utc).isoformat(),
        "missing_tables": sorted(set(source_names) - set(target_names)),
        "extra_tables": sorted(set(target_names) - set(source_names)),
        "tables": [],
    }
    total = 0
    with source.connect() as sc, target.connect() as tc:
        for name in source_names:
            checkpoint()
            if name not in target_names:
                continue
            s = sa.Table(name, sa.MetaData(), autoload_with=sc)
            t = sa.Table(name, sa.MetaData(), autoload_with=tc)
            keys = si.get_pk_constraint(name).get("constrained_columns") or []
            copied = tc.scalar(sa.select(sa.func.count()).select_from(t))
            total += copied
            cap(total, "max_source_rows")
            entry = {
                "table": name,
                "source_rows": sc.scalar(sa.select(sa.func.count()).select_from(s)),
                "copy_rows": copied,
                "source_keys": keys,
                "columns": list(s.c.keys()),
                "copy_keys": ti.get_pk_constraint(name).get("constrained_columns") or [],
                "schema_differences": table_schema_differences(si, ti, name),
                "checked_rows": 0,
                "missing_originals": 0,
                "changed_records": 0,
                "duplicate_key_groups": None,
                "examples": [],
                "relationships": [],
            }
            if keys and all(k in t.c for k in keys):
                duplicates = (
                    sa.select(*(t.c[k] for k in keys))
                    .group_by(*(t.c[k] for k in keys))
                    .having(sa.func.count() > 1)
                    .subquery()
                )
                entry["duplicate_key_groups"] = tc.scalar(
                    sa.select(sa.func.count()).select_from(duplicates)
                )
                with tc.execution_options(stream_results=True).execute(
                    sa.select(t).order_by(*(t.c[k] for k in keys))
                ) as rows:
                    while batch := rows.mappings().fetchmany(200):
                        checkpoint()
                        key_values = [tuple(row[k] for k in keys) for row in batch]
                        condition = sa.tuple_(*(s.c[k] for k in keys)).in_(key_values)
                        originals = {
                            tuple(row[k] for k in keys): dict(row)
                            for row in sc.execute(sa.select(s).where(condition)).mappings()
                        }
                        for row in batch:
                            original = originals.get(tuple(row[k] for k in keys))
                            entry["checked_rows"] += 1
                            entry["missing_originals"] += original is None
                            entry["changed_records"] += (
                                original is not None and dict(row) != original
                            )
                            if len(entry["examples"]) < 3:
                                entry["examples"].append({"original": original, "copy": dict(row)})
            for fk in si.get_foreign_keys(name):
                child_cols, parent_cols = fk["constrained_columns"], fk["referred_columns"]
                parent = fk["referred_table"]
                relationship = {
                    "from": ", ".join(child_cols),
                    "child_columns": child_cols,
                    "parent_table": parent,
                    "parent_columns": parent_cols,
                    "checked": 0,
                    "missing_parents": None,
                    "examples": [],
                }
                entry["relationships"].append(relationship)
                if parent not in target_names or not all(c in t.c for c in child_cols):
                    continue
                p = sa.Table(parent, sa.MetaData(), autoload_with=tc).alias("parent_record")
                if not all(c in p.c for c in parent_cols):
                    continue
                required = sa.and_(*(t.c[c].is_not(None) for c in child_cols))
                match = sa.and_(*(t.c[c] == p.c[r] for c, r in zip(child_cols, parent_cols)))
                relationship["checked"] = tc.scalar(
                    sa.select(sa.func.count()).select_from(t).where(required)
                )
                relationship["missing_parents"] = tc.scalar(
                    sa.select(sa.func.count())
                    .select_from(t)
                    .where(required, ~sa.exists(sa.select(1).select_from(p).where(match)))
                )
                for example in entry["examples"]:
                    values = [example["copy"][c] for c in child_cols]
                    if any(value is None for value in values):
                        continue
                    parent_row = (
                        tc.execute(
                            sa.select(p).where(
                                sa.and_(*(p.c[c] == value for c, value in zip(parent_cols, values)))
                            )
                        )
                        .mappings()
                        .first()
                    )
                    source_parent = sa.Table(parent, sa.MetaData(), autoload_with=sc)
                    original_parent = (
                        sc.execute(
                            sa.select(source_parent).where(
                                sa.and_(
                                    *(
                                        source_parent.c[c] == value
                                        for c, value in zip(parent_cols, values)
                                    )
                                )
                            )
                        )
                        .mappings()
                        .first()
                    )
                    relationship["examples"].append(
                        {
                            "child_key": {k: example["copy"][k] for k in keys},
                            "reference": dict(zip(child_cols, values)),
                            "parent_in_copy": dict(parent_row) if parent_row is not None else None,
                            "parent_in_original": dict(original_parent)
                            if original_parent is not None
                            else None,
                        }
                    )
            entry["passed"] = (
                not entry["schema_differences"]
                and bool(keys)
                and entry["checked_rows"] == copied
                and entry["duplicate_key_groups"] == 0
                and entry["missing_originals"] == 0
                and entry["changed_records"] == 0
                and all(r["missing_parents"] == 0 for r in entry["relationships"])
            )
            report["tables"].append(entry)
    report["passed"] = (
        bool(report["tables"])
        and not report["missing_tables"]
        and not report["extra_tables"]
        and all(t["passed"] for t in report["tables"])
    )
    return report


def collect_evidence(outcome):
    with copy_engines(outcome) as engines:
        report = inspect_copy(*engines)
        report.update(
            source=outcome.prepared.source.database, copy=outcome.prepared.target.database
        )
        report["file_input"] = outcome.request.kind != "mysql"
        path = outcome.folder / "copy_verification.json"
        atomic_json(path, json.loads(json.dumps(report, default=str)))
        return report
