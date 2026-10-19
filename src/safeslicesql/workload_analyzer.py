"""
safeslicesql.workload_analyzer — SQL workload parsing and feature extraction.

Reads the declared SQL workload file, identifies supported query patterns,
extracts features (join tables, filter columns, grouping, aggregates),
and runs each query against the source database to record baseline results.

Unsupported query patterns produce a clear exclusion status with a reason.

Owner: M2
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine

logger = logging.getLogger("safeslicesql.workload_analyzer")


class WorkloadAnalysis:
    """
    Parsed and analyzed workload with per-query features and source results.

    Attributes
    ----------
    queries : list[QueryInfo]
        Parsed information for each query in the workload.
    workload_path : str
        Path to the workload SQL file.
    """

    def __init__(self) -> None:
        self.queries: list[QueryInfo] = []
        self.workload_path: str = ""

    @property
    def supported_queries(self) -> list[QueryInfo]:
        """Return only queries with supported patterns."""
        return [q for q in self.queries if q.is_supported]

    @property
    def excluded_queries(self) -> list[QueryInfo]:
        """Return queries excluded due to unsupported patterns."""
        return [q for q in self.queries if not q.is_supported]


class QueryInfo:
    """
    Parsed information about a single workload query.

    Attributes
    ----------
    query_id : str
        Identifier (e.g. "Q1", "Q2").
    raw_sql : str
        The original SQL text.
    tables : list[str]
        Tables referenced in the query.
    join_tables : list[tuple[str, str]]
        Pairs of tables joined.
    filter_columns : list[tuple[str, str]]
        (table, column) pairs used in WHERE clauses.
    group_columns : list[tuple[str, str]]
        (table, column) pairs used in GROUP BY.
    aggregate_functions : list[str]
        Aggregate functions used (COUNT, SUM, etc.).
    is_supported : bool
        Whether the query pattern is within scope.
    exclusion_reason : str
        Reason for exclusion if not supported.
    source_row_count : int
        Number of rows returned from the source database.
    """

    def __init__(self, query_id: str, raw_sql: str) -> None:
        self.query_id = query_id
        self.raw_sql = raw_sql
        self.tables: list[str] = []
        self.join_tables: list[tuple[str, str]] = []
        self.filter_columns: list[tuple[str, str]] = []
        self.group_columns: list[tuple[str, str]] = []
        self.aggregate_functions: list[str] = []
        self.is_supported: bool = True
        self.exclusion_reason: str = ""
        self.source_row_count: int = 0


def load_workload(workload_path: str | Path) -> list[str]:
    """
    Load SQL queries from a workload file.

    Splits the file by semicolons, strips comments, and returns
    a list of non-empty query strings.

    Parameters
    ----------
    workload_path : str or Path
        Path to the SQL workload file.

    Returns
    -------
    list[str]
        List of SQL query strings.
    """
    path = Path(workload_path)
    if not path.exists():
        raise FileNotFoundError(f"Workload file not found at {path.resolve()}")

    content = path.read_text(encoding="utf-8")
    queries: list[str] = []

    for chunk in content.split(";"):
        cleaned_lines = [
            line.strip()
            for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        sql = " ".join(cleaned_lines).strip()
        if sql:
            queries.append(sql)

    return queries


def analyze_workload(
    workload_path: str | Path,
    engine: Engine,
) -> WorkloadAnalysis:
    """
    Parse the workload file and extract query features.

    For each query:
    1. Parse supported SQL patterns.
    2. Extract tables, joins, filters, grouping, aggregates.
    3. Execute against the source database to record baseline results.
    4. Mark unsupported patterns with a clear reason.

    Parameters
    ----------
    workload_path : str or Path
        Path to the SQL workload file.
    engine : sqlalchemy.Engine
        Source database engine for executing baseline queries.

    Returns
    -------
    WorkloadAnalysis
        Parsed workload with features and source results.
    """
    raise NotImplementedError("Phase 5: implement workload analysis")
