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
import re
from pathlib import Path

from sqlalchemy import Engine, text

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


def _extract_table_tokens(sql: str) -> list[str]:
    """Return table names appearing in FROM/JOIN clauses in order."""
    table_names: list[str] = []
    for match in re.finditer(
        r"\b(?:FROM|JOIN)\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w]*)",
        sql,
        flags=re.IGNORECASE,
    ):
        table_names.append(match.group(1))
    return table_names


def _extract_join_pairs(sql: str, tables: list[str]) -> list[tuple[str, str]]:
    """Return pairs of tables joined in the query."""
    join_pairs: list[tuple[str, str]] = []
    if len(tables) < 2:
        return join_pairs

    for current in tables[1:]:
        prev = tables[0]
        if current != prev:
            join_pairs.append((prev, current))
    return join_pairs


def _extract_column_refs(sql: str) -> list[tuple[str, str]]:
    """Parse table.column references from WHERE/HAVING/GROUP BY sections."""
    refs: list[tuple[str, str]] = []
    for match in re.finditer(
        r"([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)",
        sql,
        flags=re.IGNORECASE,
    ):
        refs.append((match.group(1), match.group(2)))
    return refs


def _extract_group_columns(sql: str) -> list[tuple[str, str]]:
    """Extract GROUP BY table.column references."""
    group_sql = re.search(r"\bGROUP\s+BY\b(.*?)(?:\bHAVING\b|\bORDER\s+BY\b|$)", sql, flags=re.IGNORECASE | re.DOTALL)
    if not group_sql:
        return []
    return _extract_column_refs(group_sql.group(1))


def _extract_aggregates(sql: str) -> list[str]:
    """Detect standard aggregate functions used in the query."""
    entries: list[str] = []
    for func in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
        if re.search(rf"\b{func}\s*\(", sql, flags=re.IGNORECASE):
            entries.append(func)
    return entries


def _is_supported_query(raw_sql: str) -> tuple[bool, str]:
    """Return whether the SQL matches the supported subset and, if not, why."""
    sql = raw_sql.strip()
    if not sql:
        return False, "Empty query"

    upper = sql.upper()
    if not upper.startswith("SELECT"):
        return False, "Only SELECT statements are supported"

    disallowed = [
        "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP",
        "TRUNCATE", "REPLACE", "MERGE", "WITH ", "UNION",
        "WINDOW", "ROW_NUMBER", "RANK", "DENSE_RANK"
    ]
    if any(token in upper for token in disallowed):
        return False, "Unsupported SQL pattern or clause"

    if re.search(r"\bLEFT\s+(?:OUTER\s+)?JOIN\b|\bRIGHT\s+(?:OUTER\s+)?JOIN\b|\bFULL\s+(?:OUTER\s+)?JOIN\b", upper):
        return False, "LEFT/RIGHT/FULL OUTER JOIN not supported in v1"

    if re.search(r"\b(?:FROM|JOIN)\b", upper) is None:
        return False, "SELECT without FROM is not supported"

    if "SELECT *" in upper and " FROM " in upper:
        # basic SELECT * is allowed in the workload file, but it is not a workload-optimization target by itself
        pass

    return True, ""


def analyze_workload(
    workload_path: str | Path,
    engine: Engine | None,
) -> WorkloadAnalysis:
    """
    Parse the workload file and extract query features.

    For each query:
    1. Parse supported SQL patterns.
    2. Extract tables, joins, filters, grouping, aggregates.
    3. Execute against the source database to record baseline results.
    4. Mark unsupported patterns with a clear reason.
    """
    analysis = WorkloadAnalysis()
    analysis.workload_path = str(workload_path)

    for index, sql in enumerate(load_workload(workload_path), start=1):
        query = QueryInfo(f"Q{index}", sql)
        cleaned = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
        query.raw_sql = cleaned.strip()
        tables = _extract_table_tokens(cleaned)
        query.tables = list(dict.fromkeys(tables))
        query.join_tables = _extract_join_pairs(cleaned, query.tables)
        query.filter_columns = _extract_column_refs(re.search(r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|$)", cleaned, flags=re.IGNORECASE | re.DOTALL).group(1) if re.search(r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|$)", cleaned, flags=re.IGNORECASE | re.DOTALL) else "")
        query.group_columns = _extract_group_columns(cleaned)
        query.aggregate_functions = _extract_aggregates(cleaned)

        is_supported, reason = _is_supported_query(cleaned)
        query.is_supported = is_supported
        query.exclusion_reason = reason

        if engine is not None and hasattr(engine, "connect"):
            try:
                with engine.connect() as conn:
                    result = conn.execute(text(cleaned))
                    rows = result.fetchall()
                    query.source_row_count = len(rows)
            except Exception as exc:
                logger.warning("Failed to execute workload query %s: %s", query.query_id, exc)
                query.is_supported = False
                query.exclusion_reason = f"Execution failed: {exc}"

        analysis.queries.append(query)

    return analysis
