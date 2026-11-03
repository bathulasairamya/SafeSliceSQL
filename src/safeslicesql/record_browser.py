"""Bounded, parameterized lookup of original/copied records and their parents."""

from contextlib import contextmanager
from datetime import datetime, timezone

import sqlalchemy as sa

from safeslicesql.runtime import bounded_engine


@contextmanager
def copy_engines(outcome):
    engines = []
    try:
        for config in (outcome.prepared.source, outcome.prepared.target):
            engines.append(bounded_engine(config.url, read_only=True))
        expected = getattr(outcome, "target_server_uuid", None)
        if expected and engines[1].dialect.name == "mysql":
            with engines[1].connect() as connection:
                if connection.exec_driver_sql("SELECT @@server_uuid").scalar_one() != expected:
                    raise ValueError(
                        "This saved copy belongs to a different server. Check Connection settings."
                    )
        yield engines
    finally:
        for engine in engines:
            engine.dispose()


def record_status(original, copy):
    if original is None:
        return "Missing from original"
    if copy is None:
        return "Outside current copy"
    return "Kept unchanged" if original == copy else "Values differ"


def _lookup(connection, table, keys):
    if not keys or any(k not in table.c for k in keys):
        return None
    row = (
        connection.execute(
            sa.select(table)
            .where(sa.and_(*(table.c[k] == value for k, value in keys.items())))
            .limit(1)
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def _pair(sc, tc, s, t, keys):
    original, copy = _lookup(sc, s, keys), _lookup(tc, t, keys)
    return {
        "table": s.name,
        "keys": keys,
        "original": original,
        "copy": copy,
        "status": record_status(original, copy),
    }


def browse_records(source, target, table, field, query, *, exact=False, offset=0, keys=None):
    """Return at most 50 records (25 from each side), with paging and parent evidence."""
    if offset < 0 or offset > 100000:
        raise ValueError("Choose a narrower search.")
    if len(query) > 500:
        raise ValueError("Search text must be at most 500 characters.")
    si, ti = sa.inspect(source), sa.inspect(target)
    if table not in si.get_table_names() or table not in ti.get_table_names():
        raise ValueError("This table is no longer available in both databases. Verify again.")
    with source.connect() as sc, target.connect() as tc:
        s = sa.Table(table, sa.MetaData(), autoload_with=sc)
        t = sa.Table(table, sa.MetaData(), autoload_with=tc)
        pk = si.get_pk_constraint(table).get("constrained_columns") or []
        if not pk or any(k not in t.c for k in pk):
            raise ValueError(
                "Record lookup needs matching record ID columns. Verify the table structure."
            )
        candidates, more = {}, False
        if keys is not None:
            if set(keys) != set(pk):
                raise ValueError("The record ID does not match this table.")
            candidates[tuple(keys[k] for k in pk)] = keys
        else:
            if field not in s.c or field not in t.c:
                raise ValueError("Choose an available search field.")
            for connection, relation in ((sc, s), (tc, t)):
                column = sa.cast(relation.c[field], sa.String)
                condition = column == query if exact else column.contains(query, autoescape=True)
                rows = (
                    connection.execute(
                        sa.select(*(relation.c[k] for k in pk))
                        .where(condition)
                        .order_by(*(relation.c[k] for k in pk))
                        .offset(offset)
                        .limit(26)
                    )
                    .mappings()
                    .all()
                )
                more |= len(rows) > 25
                for row in rows[:25]:
                    candidates[tuple(row[k] for k in pk)] = dict(row)
        records = []
        for identity in candidates.values():
            record = _pair(sc, tc, s, t, identity)
            record["connections"] = []
            child = record["copy"] if record["copy"] is not None else record["original"]
            if child is not None:
                for fk in si.get_foreign_keys(table):
                    columns, parent_columns = fk["constrained_columns"], fk["referred_columns"]
                    values = [child.get(c) for c in columns]
                    link = {
                        "reference": dict(zip(columns, values)),
                        "table": fk["referred_table"],
                        "optional": any(value is None for value in values),
                        "original": None,
                        "copy": None,
                        "keys": {},
                        "required_by_copy": record["copy"] is not None,
                    }
                    record["connections"].append(link)
                    if link["optional"]:
                        continue
                    parent_keys = dict(zip(parent_columns, values))
                    for connection, inspector, side in ((sc, si, "original"), (tc, ti, "copy")):
                        if link["table"] in inspector.get_table_names():
                            parent = sa.Table(
                                link["table"], sa.MetaData(), autoload_with=connection
                            )
                            link[side] = _lookup(connection, parent, parent_keys)
                    parent_row = link["copy"] or link["original"]
                    if parent_row:
                        parent_pk = (
                            si.get_pk_constraint(link["table"]).get("constrained_columns") or []
                        )
                        link["keys"] = {k: parent_row[k] for k in parent_pk}
            records.append(record)
        return {
            "records": records,
            "more": more,
            "offset": offset,
            "inspected_at": datetime.now(timezone.utc).isoformat(),
        }


def search_copy(outcome, table, field, query, **kwargs):
    with copy_engines(outcome) as engines:
        return browse_records(*engines, table, field, query, **kwargs)
