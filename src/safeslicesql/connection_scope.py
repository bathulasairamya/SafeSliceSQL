"""Borrow one transaction across helpers without allowing them to close it."""

from contextlib import contextmanager

import sqlalchemy as sa


class ConnectionScope:
    def __init__(self, connection):
        self.connection = connection
        self.engine = connection.engine
        self.url = self.engine.url
        self.pool = self.engine.pool
        self.dialect = self.engine.dialect

    @contextmanager
    def connect(self):
        # Streaming/no_parameters flags must not leak into subsequent DML.
        options = dict(self.connection.get_execution_options())
        try:
            yield self.connection
        finally:
            self.connection.execution_options(
                stream_results=options.get("stream_results", False),
                no_parameters=options.get("no_parameters", False),
                max_row_buffer=options.get("max_row_buffer", 1000),
            )


@sa.inspection._inspects(ConnectionScope)
def inspect_scope(scope):
    return sa.inspect(scope.connection)


@contextmanager
def source_transaction(engine):
    """Keep selection, copy and evaluation on the same repeatable read view."""
    if isinstance(engine, ConnectionScope):
        yield engine
        return
    with engine.connect() as connection:
        if engine.dialect.name == "mysql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
            connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        else:
            connection.exec_driver_sql("BEGIN")
        try:
            yield ConnectionScope(connection)
        finally:
            connection.rollback()
