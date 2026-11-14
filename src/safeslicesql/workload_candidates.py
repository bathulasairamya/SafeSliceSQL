"""Bounded row witnesses for supported FROM/JOIN/WHERE query clauses.

Witnesses preserve actual filter and join conditions. Aggregate/HAVING survival
remains a heuristic and is checked by the acceptance gate before commit.
"""

from safeslicesql.db import quote_ident
from safeslicesql.runtime import checkpoint
from safeslicesql.sql_lexer import identifier, tokenize
from safeslicesql.workload_analyzer import SQL_KEYWORDS


def query_witnesses(query, schema, engine, limit):
    if not hasattr(engine, "connect") or not query.is_supported or query.analysis_error:
        return []
    tokens = tokenize(query.raw_sql)
    compact = [token for token in tokens if token.kind != "space"]
    relations = []
    for i, token in enumerate(compact[:-1]):
        if token.kind != "word" or token.text.upper() not in {"FROM", "JOIN"}:
            continue
        name = identifier(compact[i + 1])
        if name not in schema.tables:
            return []
        alias, j = name, i + 2
        if j < len(compact) and compact[j].kind == "word" and compact[j].text.upper() == "AS":
            j += 1
        if j < len(compact):
            possible = identifier(compact[j])
            if possible and (compact[j].kind == "quoted" or possible.upper() not in SQL_KEYWORDS):
                alias = possible
        relations.append((name, alias, schema.tables[name].effective_primary_keys))
    if not relations:
        return []
    start, end, depth = None, len(tokens), 0
    for i, token in enumerate(tokens):
        if depth == 0 and token.kind == "word":
            word = token.text.upper()
            if word == "FROM" and start is None:
                start = i
            elif start is not None and word in {"GROUP", "HAVING", "ORDER", "LIMIT"}:
                end = i
                break
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
    if start is None:
        return []
    columns = [
        f"{quote_ident(alias)}.{quote_ident(key)}" for _, alias, keys in relations for key in keys
    ]
    clause = "".join(token.text for token in tokens[start:end]).rstrip().rstrip(";")
    sql = f"SELECT DISTINCT {', '.join(columns)} {clause} ORDER BY {', '.join(columns)} LIMIT {int(limit)}"
    witnesses = []
    with engine.connect() as connection:
        with connection.execution_options(no_parameters=True).exec_driver_sql(sql) as result:
            for row in result:
                checkpoint()
                witness, offset = {}, 0
                for name, _, keys in relations:
                    key = row[offset] if len(keys) == 1 else tuple(row[offset : offset + len(keys)])
                    witness.setdefault(name, set()).add(key)
                    offset += len(keys)
                witnesses.append(witness)
    return witnesses
