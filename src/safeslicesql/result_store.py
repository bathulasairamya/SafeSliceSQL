"""Disk-backed exact result comparison with bounded Python memory."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from safeslicesql.runtime import checkpoint, current


class ResultStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        connection.execute("PRAGMA cache_size=-4096")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA journal_mode=OFF")
        if guard := current():
            pages = max(
                2,
                guard.limits.max_temp_bytes // connection.execute("PRAGMA page_size").fetchone()[0],
            )
            connection.execute(f"PRAGMA max_page_count={pages}")
        connection.execute(
            "CREATE TABLE results (kind INTEGER, value TEXT, source INTEGER, target INTEGER, "
            "PRIMARY KEY(kind,value)) WITHOUT ROWID"
        )

    def add_batch(self, rows: list[tuple[int, str, int, int]]) -> None:
        checkpoint()
        self.connection.executemany(
            "INSERT INTO results VALUES(?,?,?,?) ON CONFLICT(kind,value) DO UPDATE SET "
            "source=source+excluded.source, target=target+excluded.target",
            rows,
        )

    def retention(self, kind: int, *, multiset: bool = False) -> float:
        if multiset:
            sql = "SELECT SUM(MIN(source,target)), SUM(source) FROM results WHERE kind=?"
        else:
            sql = "SELECT SUM(source>0 AND target>0), SUM(source>0) FROM results WHERE kind=?"
        retained, total = self.connection.execute(sql, (kind,)).fetchone()
        return (retained or 0) / total if total else 0.0


def encode_values(values) -> str:
    """Preserve every output position and the existing typed-value semantics."""
    return json.dumps([(type(value).__name__, str(value)) for value in values], ensure_ascii=True)


@contextmanager
def temporary_result_store():
    """Delete temporary result data even if a query or comparison fails."""
    with TemporaryDirectory(prefix="safeslice-results-") as directory:
        connection = sqlite3.connect(Path(directory) / "results.sqlite")
        try:
            yield ResultStore(connection)
        finally:
            connection.close()
