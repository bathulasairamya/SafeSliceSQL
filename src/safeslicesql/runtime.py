"""Cooperative cancellation, deadlines, and bounded database/resource work."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields
from threading import Event

import psutil
import sqlalchemy as sa


class OperationInterrupted(BaseException):
    """Bypass generic error handlers so transactions unwind and roll back."""


class OperationCancelled(OperationInterrupted):
    pass


class LimitExceeded(OperationInterrupted):
    pass


@dataclass(frozen=True)
class Limits:
    job_seconds: int = 600
    statement_seconds: int = 30
    connect_seconds: int = 10
    max_source_rows: int = 5_000_000
    max_result_rows: int = 1_000_000
    max_input_bytes: int = 100 * 1024 * 1024
    max_cell_bytes: int = 1024 * 1024
    max_temp_bytes: int = 512 * 1024 * 1024
    max_memory_bytes: int = 1024 * 1024 * 1024
    max_tables: int = 100
    max_columns: int = 500

    def __post_init__(self):
        if any(
            isinstance(getattr(self, field.name), bool)
            or not isinstance(getattr(self, field.name), int)
            or getattr(self, field.name) <= 0
            for field in fields(self)
        ):
            raise ValueError("Resource limits must be positive integers.")

    @classmethod
    def from_env(cls):
        values = {}
        for field in fields(cls):
            raw = os.environ.get("SAFESLICE_LIMIT_" + field.name.upper())
            if raw is not None:
                values[field.name] = int(raw)
        return cls(**values)


class Guard:
    def __init__(self, limits: Limits, cancellation: Event | None = None, job=None):
        self.limits, self.cancellation, self.job = limits, cancellation or Event(), job
        self.started = time.monotonic()
        self.failure = None
        self.last_memory_check = 0.0

    def check(self):
        if self.failure:
            raise self.failure
        if self.cancellation.is_set() or (self.job and self.job.cancel_requested()):
            raise OperationCancelled(
                "Operation cancelled. A committed target, if any, requires review."
            )
        now = time.monotonic()
        if now - self.started >= self.limits.job_seconds:
            raise LimitExceeded("Job time limit exceeded.")
        if now - self.last_memory_check >= 0.5:
            self.last_memory_check = now
            if psutil.Process().memory_info().rss > self.limits.max_memory_bytes:
                raise LimitExceeded("Process memory budget exceeded.")

    def cap(self, amount: int, name: str):
        self.check()
        if amount > getattr(self.limits, name):
            raise LimitExceeded(f"Resource limit exceeded: {name}.")


_active: ContextVar[Guard | None] = ContextVar("safeslice_guard", default=None)


def current() -> Guard | None:
    return _active.get()


def checkpoint():
    if guard := current():
        guard.check()


@contextmanager
def suspend_limits():
    """Allow a short owned-resource cleanup after a cancellation/deadline."""
    token = _active.set(None)
    try:
        yield
    finally:
        _active.reset(token)


def cap(amount: int, name: str):
    if guard := current():
        guard.cap(amount, name)


@contextmanager
def execution_context(limits: Limits | None = None, cancellation: Event | None = None, job=None):
    guard = Guard(limits or Limits.from_env(), cancellation, job)
    token = _active.set(guard)
    try:
        guard.check()
        yield guard
        guard.check()
    except Exception:
        guard.check()
        raise
    finally:
        _active.reset(token)


def bounded_engine(url, *, read_only: bool = False, **kwargs):
    """Apply limits only within a bounded job; library callers can opt in."""
    guard = current()
    parsed = sa.engine.make_url(url)
    if guard and parsed.get_backend_name() == "mysql":
        args = dict(kwargs.pop("connect_args", {}))
        args.setdefault("connect_timeout", guard.limits.connect_seconds)
        args.setdefault("read_timeout", guard.limits.statement_seconds + 5)
        args.setdefault("write_timeout", guard.limits.statement_seconds + 5)
        kwargs["connect_args"] = args
    engine = sa.create_engine(url, **kwargs)
    if not isinstance(engine, sa.Engine):
        return engine
    if read_only:

        @sa.event.listens_for(engine, "connect")
        def readonly(connection, record):
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SET SESSION TRANSACTION READ ONLY"
                    if engine.dialect.name == "mysql"
                    else "PRAGMA query_only=ON"
                )
            finally:
                cursor.close()

    if guard is None:
        return engine

    @sa.event.listens_for(engine, "before_cursor_execute")
    def before(connection, cursor, statement, parameters, context, executemany):
        if current() is not guard:
            return
        guard.check()
        if engine.dialect.name == "sqlite":
            started = time.monotonic()

            def progress():
                if current() is not guard:
                    return 0
                try:
                    guard.check()
                    if time.monotonic() - started >= guard.limits.statement_seconds:
                        raise LimitExceeded("Statement time limit exceeded.")
                except OperationInterrupted as exc:
                    guard.failure = exc
                    return 1
                return 0

            connection.connection.driver_connection.set_progress_handler(progress, 1000)

    if engine.dialect.name == "mysql":

        @sa.event.listens_for(engine, "connect")
        def configure(connection, record):
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SET SESSION MAX_EXECUTION_TIME={guard.limits.statement_seconds * 1000}"
                )
                cursor.execute(
                    f"SET SESSION innodb_lock_wait_timeout={guard.limits.statement_seconds}"
                )
                cursor.execute(f"SET SESSION lock_wait_timeout={guard.limits.statement_seconds}")

    return engine
