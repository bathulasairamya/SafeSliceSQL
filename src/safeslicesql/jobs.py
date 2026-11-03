"""Local-user job journal, interruption recovery, and owned-schema retention."""

from __future__ import annotations

import json
import math
import os
import re
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psutil

from safeslicesql.db import quote_ident
from safeslicesql.runtime import OperationCancelled, OperationInterrupted, current
from safeslicesql.security import safe_error


def atomic_json(path: Path, value: dict):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_job(path: Path) -> dict:
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("Invalid job record.")
    if (
        record.get("owner") != psutil.Process().username()
        or record.get("host") != socket.gethostname()
    ):
        raise PermissionError("This job belongs to another local user or host.")
    if (
        record.get("state")
        not in {"running", "completed", "rejected", "failed", "cancelled", "limited", "interrupted"}
        or not isinstance(record.get("phase"), str)
        or not isinstance(record.get("pid"), int)
        or isinstance(record["pid"], bool)
        or record["pid"] < 1
        or not isinstance(record.get("request"), dict)
        or not isinstance(record.get("schemas"), list)
    ):
        raise ValueError("Invalid job record.")
    for key in ("created_at", "updated_at", "process_started"):
        value = record.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("Invalid job timestamps.")
    for schema in record["schemas"]:
        if (
            not isinstance(schema, dict)
            or not isinstance(schema.get("name"), str)
            or not isinstance(schema.get("server_uuid"), str)
            or schema.get("state") not in {"created", "removed"}
        ):
            raise ValueError("Invalid schema ownership record.")
    return record


class Job:
    def __init__(self, folder: Path, request: dict | None = None):
        folder.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            folder.chmod(0o700)
        if (folder / "job.json").exists():
            raise FileExistsError("A job record already exists in this folder.")
        self.folder = folder
        self.path = folder / "job.json"
        self.record = dict(
            owner=psutil.Process().username(),
            host=socket.gethostname(),
            pid=os.getpid(),
            process_started=psutil.Process().create_time(),
            created_at=time.time(),
            updated_at=time.time(),
            state="running",
            phase="starting",
            request=request or {},
            schemas=[],
            error="",
        )
        self.save()

    def save(self):
        self.record["updated_at"] = time.time()
        atomic_json(self.path, self.record)

    def phase(self, phase):
        self.record["phase"] = phase
        self.save()

    def cancel_requested(self):
        return (self.folder / "cancel.requested").exists()

    def finish(self, state, error=""):
        self.record.update(state=state, error=error)
        self.save()


@contextmanager
def journal(folder: Path, request: dict | None = None):
    job = Job(folder, request)
    try:
        yield job
    except OperationInterrupted as exc:
        job.finish(
            "cancelled" if isinstance(exc, OperationCancelled) else "limited", safe_error(exc)
        )
        raise
    except BaseException as exc:
        job.finish(
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", safe_error(exc)
        )
        raise
    else:
        if job.record["state"] == "running":
            job.finish("completed")


def register_schema(admin, name: str, role: str):
    guard = current()
    if guard is None or guard.job is None:
        return
    with admin.connect() as connection:
        server = connection.exec_driver_sql("SELECT @@server_uuid").scalar_one()
    guard.job.record["schemas"].append(
        dict(name=name, role=role, server_uuid=server, state="created")
    )
    if name in guard.job.record.get("pending_schemas", []):
        guard.job.record["pending_schemas"].remove(name)
    guard.job.save()


def intend_schema(name: str):
    if (guard := current()) and guard.job:
        guard.job.record.setdefault("pending_schemas", []).append(name)
        guard.job.save()


def removed_schema(name: str):
    if (guard := current()) and guard.job:
        for schema in guard.job.record["schemas"]:
            if schema["name"] == name:
                schema["state"] = "removed"
        if name in guard.job.record.get("pending_schemas", []):
            guard.job.record["pending_schemas"].remove(name)
        guard.job.save()


def owned_jobs(root: Path):
    root = root.resolve()
    if not root.exists():
        return
    for path in root.rglob("job.json"):
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            continue
        try:
            yield path, read_job(path)
        except (OSError, ValueError, PermissionError):
            continue


def recover_jobs(root: Path) -> list[Path]:
    recovered = []
    for path, record in owned_jobs(root):
        if record["state"] != "running":
            continue
        try:
            alive = (
                abs(psutil.Process(record["pid"]).create_time() - record["process_started"]) < 0.01
            )
        except psutil.AccessDenied:
            continue
        except psutil.Error:
            alive = False
        if not alive:
            record.update(
                state="interrupted",
                error="The owning process ended. Restart from the original input; do not publish this target.",
                updated_at=time.time(),
            )
            atomic_json(path, record)
            recovered.append(path)
    return recovered


def cleanup_expired(root: Path, config, days: int = 7) -> list[str]:
    """Explicit cleanup: exact journaled names, matching server, terminal jobs only."""
    from safeslicesql.runtime import execution_context

    if current() is not None:
        return _cleanup_expired(root, config, days)
    with execution_context():
        return _cleanup_expired(root, config, days)


def _cleanup_expired(root: Path, config, days: int) -> list[str]:
    from safeslicesql.runtime import bounded_engine

    if days < 1:
        raise ValueError("Retention must be at least one day.")
    recover_jobs(root)
    candidates = [
        (path, record)
        for path, record in owned_jobs(root)
        if record["state"] != "running" and time.time() - record["updated_at"] >= days * 86400
    ]
    if not candidates:
        return []
    admin = bounded_engine(config.target_db.url.set(database=None))
    removed = []
    try:
        with admin.connect() as connection:
            server = connection.exec_driver_sql("SELECT @@server_uuid").scalar_one()
        for path, record in candidates:
            for schema in record["schemas"]:
                if schema["state"] != "created":
                    continue
                if schema["server_uuid"] != server or not re.fullmatch(
                    r"safeslice_(?:(?:input|output)_[a-f0-9]{16}|check_[a-f0-9]{32}_(?:source|target))",
                    schema["name"],
                ):
                    raise ValueError("Refusing cleanup: database ownership/server checks failed.")
                with admin.begin() as connection:
                    connection.exec_driver_sql(
                        f"DROP DATABASE IF EXISTS {quote_ident(schema['name'])}"
                    )
                schema["state"] = "removed"
                atomic_json(path, record)
                removed.append(schema["name"])
    finally:
        admin.dispose()
    return removed
