"""Credential-free saved results and read-only reopening of existing copies."""

import csv
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from safeslicesql.jobs import atomic_json, read_job


def save_copy_summary(outcome):
    run = outcome.results.runs[-1] if outcome.results.runs else None
    validation = run.validation if run else None
    generated = run.generation if run else None
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": outcome.prepared.source.database,
        "target": outcome.prepared.target.database,
        "file_input": outcome.request.kind != "mysql",
        "budget": outcome.request.budget,
        "method": run.method if run else outcome.request.method,
        "ready": bool(run and run.success and not outcome.results.failed_runs),
        "source_rows": sum(outcome.prepared.tables.values()),
        "copy_rows": validation.actual_rows if validation else None,
        "validation": asdict(validation) if validation else None,
        "tables": [
            {
                "table": name,
                "source_rows": rows,
                "copy_rows": generated.rows_inserted.get(name, 0)
                if generated and generated.success
                else None,
            }
            for name, rows in outcome.prepared.tables.items()
        ],
        "error": "; ".join(r.error_message for r in outcome.results.runs if r.error_message),
    }
    atomic_json(outcome.folder / "copy_summary.json", summary)
    return summary


def load_summary(folder):
    folder = Path(folder)
    path = folder / "copy_summary.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    # Older jobs remain readable without creating a new copy or connecting to MySQL.
    job = read_job(folder / "job.json")
    manifest = json.loads((folder / "dataset" / "dataset.json").read_text(encoding="utf-8"))
    summary = {
        "created_at": datetime.fromtimestamp(job["created_at"], timezone.utc).isoformat(),
        "source": manifest["source"],
        "target": manifest["target"],
        "file_input": job["request"].get("kind") != "mysql",
        "budget": job["request"].get("budget"),
        "method": job["request"].get("method"),
        "ready": job["state"] == "completed",
        "source_rows": sum(manifest["tables"].values()),
        "copy_rows": None,
        "validation": None,
        "error": job.get("error", ""),
        "tables": [
            {"table": name, "source_rows": rows, "copy_rows": None}
            for name, rows in manifest["tables"].items()
        ],
    }
    csv_path = folder / "raw" / "all_benchmark_runs.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            runs = list(csv.DictReader(handle))
        if runs:
            last = runs[-1]
            summary["copy_rows"] = (
                int(float(last["actual_rows"])) if last.get("actual_rows") else None
            )
            summary["method"] = last.get("method")
    return summary


def saved_outcome(folder, config):
    folder = Path(folder)
    job = read_job(folder / "job.json")
    if job["state"] in {"running", "interrupted", "cancelled", "limited"}:
        raise ValueError(
            "This job did not finish. View its saved reports; restart from the original input for a usable copy."
        )
    summary = load_summary(folder)
    target = next(
        (s for s in job["schemas"] if s["name"] == summary["target"] and s["state"] == "created"),
        None,
    )
    if target is None:
        raise ValueError(
            "This copy has been removed or its ownership record is unavailable. Saved reports remain available."
        )
    source_config = config.target_db if summary["file_input"] else config.source_db
    return SimpleNamespace(
        folder=folder,
        request=SimpleNamespace(kind="csv" if summary["file_input"] else "mysql"),
        prepared=SimpleNamespace(
            source=replace(source_config, database=summary["source"]),
            target=replace(config.target_db, database=summary["target"]),
        ),
        target_server_uuid=target["server_uuid"],
    )


def read_evidence(folder):
    path = Path(folder) / "copy_verification.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
