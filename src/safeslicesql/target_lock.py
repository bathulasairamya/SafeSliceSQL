"""Serialize copying and evaluation for a target across local workers."""

import hashlib
import os
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

import sqlalchemy as sa

from safeslicesql.runtime import checkpoint, suspend_limits
from safeslicesql.security import safe_error
from safeslicesql.types import BenchmarkRunResult


@contextmanager
def target_lease(engine, timeout: float = 5):
    checkpoint()
    if engine.dialect.name == "mysql":
        connection = engine.connect()
        acquired = False
        try:
            database, insensitive = connection.exec_driver_sql(
                "SELECT DATABASE(), @@lower_case_table_names"
            ).one()
            name = database.lower() if insensitive else database
            lock = "safeslice:" + hashlib.sha256(name.encode()).hexdigest()[:48]
            acquired = (
                connection.execute(
                    sa.text("SELECT GET_LOCK(:name,:timeout)"),
                    {"name": lock, "timeout": int(timeout)},
                ).scalar_one()
                == 1
            )
            if not acquired:
                raise ValueError("Target database is busy with another copy/evaluation.")
            checkpoint()
            yield
        finally:
            if acquired:
                try:
                    with suspend_limits():
                        connection.execute(sa.text("SELECT RELEASE_LOCK(:name)"), {"name": lock})
                except Exception:
                    connection.invalidate()
            connection.close()
    else:
        with engine.connect() as connection:
            filename = next(
                row[2]
                for row in connection.exec_driver_sql("PRAGMA database_list")
                if row[1] == "main"
            )
        if not filename:  # Private in-memory SQLite databases have no cross-process identity.
            yield
            return
        path = Path(filename + ".safeslice.lock")
        with path.open("a+b") as handle:
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                checkpoint()
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError("Target database is busy with another copy/evaluation.")
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def serialized_target(function):
    @wraps(function)
    def run(**options):
        try:
            with target_lease(options["target_engine"]):
                return function(**options)
        except Exception as exc:
            return BenchmarkRunResult(
                options["run_id"],
                options["method"],
                options["seed"],
                options["budget_percent"],
                options["schema"].total_row_count,
                error_message=safe_error(exc),
            )

    return run
