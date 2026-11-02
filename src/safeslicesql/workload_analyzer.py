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

import sqlalchemy as sa
from sqlalchemy import Engine

from safeslicesql.sql_lexer import identifier, split_statements, syntax_only, tokenize

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
        self.source_evaluated: bool = False
        self.analysis_error: str = ""
        self.group_output_positions: list[int] | None = None


def require_usable_workload(
    analysis: WorkloadAnalysis | None, *, for_selection: bool = False
) -> None:
    """Reject an absent/empty experiment rather than silently changing methods."""
    if analysis is None or not analysis.supported_queries:
        raise ValueError("Workload must contain at least one supported SELECT query.")
    if for_selection and not any(
        query.tables and not query.analysis_error for query in analysis.supported_queries
    ):
        raise ValueError("Workload-aware selection requires at least one usable workload query.")


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

    return split_statements(path.read_text(encoding="utf-8-sig"))


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

    tokens = [token for token in tokenize(sql) if token.kind != "space"]
    for index, token in enumerate(tokens[:-1]):
        if token.kind != "word" or token.text.upper() not in {"FROM", "JOIN"}:
            continue
        table_token = tokens[index + 1]
        tbl = identifier(table_token)
        if tbl is None or (table_token.kind == "word" and tbl.upper() in SQL_KEYWORDS):
            continue
        next_index = index + 2
        if next_index < len(tokens) and tokens[next_index].text == ".":
            # Qualified databases would also pin target evaluation to the source.
            raise ValueError("Database-qualified workload tables are not supported.")
        if tbl not in table_names:
            table_names.append(tbl)
        alias_map[tbl] = tbl
        alias_map[tbl.lower()] = tbl
        if (
            next_index < len(tokens)
            and tokens[next_index].kind == "word"
            and tokens[next_index].text.upper() == "AS"
        ):
            next_index += 1
        if next_index < len(tokens):
            alias_token = tokens[next_index]
            alias = identifier(alias_token)
            if alias and (alias_token.kind == "quoted" or alias.upper() not in SQL_KEYWORDS):
                alias_map[alias] = tbl
                alias_map[alias.lower()] = tbl

    return table_names, alias_map


def _tokens(sql: str):
    return [token for token in tokenize(sql) if token.kind != "space"]


def _clause_tokens(sql: str, start: tuple[str, ...], endings: set[str]):
    tokens = _tokens(sql)
    depth = 0
    found = None
    for index, token in enumerate(tokens):
        if depth == 0:
            if found is None and all(
                index + offset < len(tokens)
                and tokens[index + offset].kind == "word"
                and tokens[index + offset].text.upper() == word
                for offset, word in enumerate(start)
            ):
                found = index + len(start)
            elif (
                found is not None
                and index >= found
                and token.kind == "word"
                and token.text.upper() in endings
            ):
                return tokens[found:index]
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
    return tokens[found:] if found is not None else []


def _split_expressions(tokens):
    expressions, current, depth = [], [], 0
    for token in tokens:
        if token.text == "," and depth == 0:
            expressions.append(current)
            current = []
            continue
        current.append(token)
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
    if current:
        expressions.append(current)
    return expressions


def _references(tokens, alias_map, tables):
    refs, index = [], 0
    while index < len(tokens):
        token = tokens[index]
        name = identifier(token)
        if name is None or (token.kind == "word" and name.upper() in SQL_KEYWORDS):
            index += 1
            continue
        if index + 2 < len(tokens) and tokens[index + 1].text == ".":
            column = identifier(tokens[index + 2])
            if column:
                ref = (alias_map.get(name, alias_map.get(name.lower(), name)), column)
                if ref not in refs:
                    refs.append(ref)
            index += 3
            continue
        if index + 1 < len(tokens) and tokens[index + 1].text == "(":
            index += 1  # Function name, not a column.
            continue
        if len(tables) == 1:
            ref = (tables[0], name)
            if ref not in refs:
                refs.append(ref)
        index += 1
    return refs


def _extract_join_pairs(sql, tables, alias_map):
    pairs = []
    tokens = _tokens(sql)
    for index, token in enumerate(tokens):
        if token.text != "=" or index < 3 or index + 3 >= len(tokens):
            continue
        left, right = tokens[index - 3 : index], tokens[index + 1 : index + 4]
        if left[1].text == right[1].text == ".":
            left_refs, right_refs = (
                _references(left, alias_map, tables),
                _references(right, alias_map, tables),
            )
            if left_refs and right_refs and left_refs[0][0] != right_refs[0][0]:
                pair = tuple(sorted((left_refs[0][0], right_refs[0][0])))
                if pair not in pairs:
                    pairs.append(pair)
    return pairs or list(zip(tables, tables[1:]))


def _extract_column_refs(clause, alias_map, tables):
    return _references(_tokens(clause), alias_map, tables)


def _projection_expressions(sql):
    tokens = _clause_tokens(sql, ("SELECT",), {"FROM"})
    if tokens and tokens[0].kind == "word" and tokens[0].text.upper() in {"DISTINCT", "ALL"}:
        tokens = tokens[1:]
    projections = []
    for expression in _split_expressions(tokens):
        alias = None
        if (
            len(expression) >= 3
            and expression[-2].kind == "word"
            and expression[-2].text.upper() == "AS"
        ):
            alias = identifier(expression[-1])
            expression = expression[:-2]
        elif (
            len(expression) >= 2
            and identifier(expression[-1])
            and (identifier(expression[-2]) or expression[-2].text == ")")
        ):
            alias = identifier(expression[-1])
            expression = expression[:-1]
        projections.append((expression, alias))
    return projections


def _expression_key(tokens, alias_map, tables):
    # Resolve the common simple column forms, including qualified/unqualified pairs.
    refs = _references(tokens, alias_map, tables)
    if len(refs) == 1 and (len(tokens) == 1 or (len(tokens) == 3 and tokens[1].text == ".")):
        return tuple(part.lower() for part in refs[0])
    return tuple((token.kind, token.text.lower()) for token in tokens)


def _group_features(sql, alias_map, tables):
    projections = _projection_expressions(sql)
    group_tokens = _clause_tokens(sql, ("GROUP", "BY"), {"HAVING", "ORDER", "LIMIT"})
    groups = _split_expressions(group_tokens)
    refs, positions = [], []
    projection_keys = [
        _expression_key(expression, alias_map, tables) for expression, _ in projections
    ]
    for group in groups:
        expression = group
        position = None
        raw = "".join(token.text for token in group)
        if raw.isdigit() and 1 <= int(raw) <= len(projections):
            position = int(raw) - 1
            expression = projections[position][0]
        elif len(group) == 1 and identifier(group[0]):
            matches = [
                i
                for i, (_, alias) in enumerate(projections)
                if alias and alias.lower() == identifier(group[0]).lower()
            ]
            if len(matches) == 1:
                position = matches[0]
                expression = projections[position][0]
        if position is None:
            key = _expression_key(expression, alias_map, tables)
            matches = [i for i, candidate in enumerate(projection_keys) if candidate == key]
            if len(matches) == 1:
                position = matches[0]
        positions.append(position)
        for ref in _references(expression, alias_map, tables):
            if ref not in refs:
                refs.append(ref)
    # Wildcard expansion shifts output positions; don't guess ambiguous mappings.
    has_wildcard = any(
        any(token.text == "*" for token in expression)
        and not any(token.text == "(" for token in expression)
        for expression, _ in projections
    )
    mapped = positions if groups and None not in positions and not has_wildcard else None
    return refs, mapped


def _extract_group_columns(sql, alias_map, tables):
    return _group_features(sql, alias_map, tables)[0]


def _extract_aggregates(sql: str) -> list[str]:
    """Detect standard aggregate functions used in the query."""
    sql = syntax_only(sql)
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

    upper = syntax_only(sql).upper()
    if not re.match(r"SELECT\b", upper):
        return False, "Only SELECT statements are supported"

    if len(re.findall(r"\bSELECT\b", upper)) != 1:
        return False, "Subqueries are not supported"

    disallowed_pattern = re.compile(
        r"\b(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|REPLACE"
        r"|MERGE|UNION|INTERSECT|EXCEPT|WINDOW|OVER|ROW_NUMBER|RANK|DENSE_RANK|INTO)\b"
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
        cleaned = sql
        query.raw_sql = cleaned.strip()
        try:
            tables, alias_map = _extract_tables_and_aliases(cleaned)
        except ValueError as exc:
            query.is_supported = False
            query.exclusion_reason = str(exc)
            analysis.queries.append(query)
            continue
        query.tables = tables
        query.join_tables = _extract_join_pairs(cleaned, query.tables, alias_map)

        where_tokens = _clause_tokens(cleaned, ("WHERE",), {"GROUP", "HAVING", "ORDER", "LIMIT"})
        query.filter_columns = _references(where_tokens, alias_map, tables)
        query.group_columns, query.group_output_positions = _group_features(
            cleaned, alias_map, tables
        )
        query.aggregate_functions = _extract_aggregates(cleaned)

        is_supported, reason = _is_supported_query(cleaned)
        query.is_supported = is_supported
        query.exclusion_reason = reason
        if query.is_supported and not tables:
            query.is_supported = False
            query.exclusion_reason = "Could not resolve workload table names."

        if available_tables is not None and query.tables:
            missing_tables = [t for t in query.tables if t not in available_tables]
            if missing_tables:
                db_name = (
                    getattr(engine.url, "database", "source")
                    if hasattr(engine, "url")
                    else "source"
                )
                logger.info(
                    "Skipping query %s: table(s) %s do not exist in database '%s'",
                    query.query_id,
                    missing_tables,
                    db_name,
                )
                query.analysis_error = f"Table(s) {missing_tables} not in database"
                analysis.queries.append(query)
                continue

        if not query.is_supported:
            analysis.queries.append(query)
            continue

        if engine is not None and hasattr(engine, "connect"):
            try:
                with engine.connect() as conn:
                    result = conn.execution_options(
                        no_parameters=True, stream_results=True, max_row_buffer=1000
                    ).exec_driver_sql(cleaned)
                    try:
                        query.source_row_count = sum(1 for _ in result)
                    finally:
                        result.close()
                    query.source_evaluated = True
            except Exception as exc:
                logger.warning("Failed to execute workload query %s: %s", query.query_id, exc)
                query.analysis_error = f"Execution failed: {exc}"

        analysis.queries.append(query)

    return analysis
