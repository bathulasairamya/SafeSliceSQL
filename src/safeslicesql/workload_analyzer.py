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


SQL_KEYWORDS = {
    "WHERE",
    "JOIN",
    "ON",
    "INNER",
    "LEFT",
    "RIGHT",
    "CROSS",
    "NATURAL",
    "GROUP",
    "ORDER",
    "HAVING",
    "LIMIT",
    "SET",
    "USING",
    "AS",
    "BY",
    "AND",
    "OR",
    "NOT",
    "IN",
    "IS",
    "NULL",
    "LIKE",
    "BETWEEN",
    "SELECT",
    "FROM",
    "ASC",
    "DESC",
    "DISTINCT",
    "ALL",
    "EXISTS",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "TRUE",
    "FALSE",
}


def _extract_tables_and_aliases(sql: str) -> tuple[list[str], dict[str, str]]:
    """
    Extract table names and alias mappings from FROM and JOIN clauses.

    Returns
    -------
    tuple[list[str], dict[str, str]]
        List of unique table names and mapping of alias -> table_name.
    """
    table_names: list[str] = []
    alias_map: dict[str, str] = {}

    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)(?:\s+(?:AS\s+)?([A-Za-z_][\w]*))?",
        re.IGNORECASE,
    )

    for match in pattern.finditer(sql):
        tbl = match.group(1)
        alias = match.group(2)

        if tbl.upper() in SQL_KEYWORDS:
            continue

        if tbl not in table_names:
            table_names.append(tbl)
        alias_map[tbl] = tbl
        alias_map[tbl.lower()] = tbl

        if alias and alias.upper() not in SQL_KEYWORDS:
            alias_map[alias] = tbl
            alias_map[alias.lower()] = tbl

    return table_names, alias_map


def _extract_join_pairs(
    sql: str, tables: list[str], alias_map: dict[str, str]
) -> list[tuple[str, str]]:
    """Return pairs of tables joined in the query."""
    join_pairs: list[tuple[str, str]] = []

    # Try parsing explicit ON clauses: ON a.col = b.col
    on_pattern = re.compile(
        r"\bON\s+([A-Za-z_][\w]*)\.[A-Za-z_][\w]*\s*=\s*([A-Za-z_][\w]*)\.[A-Za-z_][\w]*",
        re.IGNORECASE,
    )
    for match in on_pattern.finditer(sql):
        t1 = alias_map.get(match.group(1), match.group(1))
        t2 = alias_map.get(match.group(2), match.group(2))
        if t1 != t2:
            pair = tuple(sorted([t1, t2]))
            if pair not in join_pairs:
                join_pairs.append(pair)

    if not join_pairs and len(tables) >= 2:
        for i in range(1, len(tables)):
            pair = (tables[i - 1], tables[i])
            if pair not in join_pairs:
                join_pairs.append(pair)

    return join_pairs


def _extract_column_refs(
    clause: str,
    alias_map: dict[str, str],
    tables: list[str],
) -> list[tuple[str, str]]:
    """Parse table.column references with alias resolution and single-table fallback."""
    if not clause:
        return []

    refs: list[tuple[str, str]] = []

    # 1. Qualified references: alias.column or table.column
    for match in re.finditer(r"([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)", clause):
        prefix, col = match.group(1), match.group(2)
        resolved_table = alias_map.get(prefix, alias_map.get(prefix.lower(), prefix))
        ref = (resolved_table, col)
        if ref not in refs:
            refs.append(ref)

    # 2. Single-table unqualified column fallback: col > val
    if not refs and len(tables) == 1:
        tbl = tables[0]
        for match in re.finditer(
            r"\b([A-Za-z_][\w]*)\s*(?:[=><!]|LIKE|IN|IS|BETWEEN)",
            clause,
            re.IGNORECASE,
        ):
            col = match.group(1)
            if col.upper() not in SQL_KEYWORDS:
                ref = (tbl, col)
                if ref not in refs:
                    refs.append(ref)

    return refs


def _extract_group_columns(
    sql: str,
    alias_map: dict[str, str],
    tables: list[str],
) -> list[tuple[str, str]]:
    """Extract GROUP BY table.column references with alias resolution."""
    group_sql = re.search(
        r"\bGROUP\s+BY\b(.*?)(?:\bHAVING\b|\bORDER\s+BY\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not group_sql:
        return []
    return _extract_column_refs(group_sql.group(1), alias_map, tables)


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

    disallowed_pattern = re.compile(
        r"\b(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|REPLACE"
        r"|MERGE|UNION|WINDOW|ROW_NUMBER|RANK|DENSE_RANK)\b"
        r"|(?:\bWITH\b\s+\w+\s+AS\b)",
        re.IGNORECASE,
    )
    if disallowed_pattern.search(upper):
        return False, "Unsupported SQL pattern or clause"

    if re.search(
        r"\bLEFT\s+(?:OUTER\s+)?JOIN\b|\bRIGHT\s+(?:OUTER\s+)?JOIN\b|\bFULL\s+(?:OUTER\s+)?JOIN\b",
        upper,
    ):
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

    available_tables: set[str] | None = None
    if engine is not None and hasattr(engine, "connect"):
        try:
            insp = sa.inspect(engine)
            available_tables = set(insp.get_table_names())
        except Exception:
            available_tables = None

    for index, sql in enumerate(load_workload(workload_path), start=1):
        query = QueryInfo(f"Q{index}", sql)
        cleaned = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
        query.raw_sql = cleaned.strip()
        tables, alias_map = _extract_tables_and_aliases(cleaned)
        query.tables = tables
        query.join_tables = _extract_join_pairs(cleaned, query.tables, alias_map)

        where_match = re.search(
            r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|$)",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        where_clause = where_match.group(1) if where_match else ""
        query.filter_columns = _extract_column_refs(where_clause, alias_map, query.tables)
        query.group_columns = _extract_group_columns(cleaned, alias_map, query.tables)
        query.aggregate_functions = _extract_aggregates(cleaned)

        is_supported, reason = _is_supported_query(cleaned)
        query.is_supported = is_supported
        query.exclusion_reason = reason

        if available_tables is not None and query.tables:
            missing_tables = [t for t in query.tables if t not in available_tables]
            if missing_tables:
                db_name = getattr(engine.url, "database", "source") if hasattr(engine, "url") else "source"
                logger.info(
                    "Skipping query %s: table(s) %s do not exist in database '%s'",
                    query.query_id,
                    missing_tables,
                    db_name,
                )
                query.is_supported = False
                query.exclusion_reason = f"Table(s) {missing_tables} not in database"
                analysis.queries.append(query)
                continue

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
