"""Read-only input statistics before creating any source import or target schema."""

from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

from safeslicesql.dataset_input import iter_input_tables
from safeslicesql.runtime import bounded_engine, cap
from safeslicesql.schema_analyzer import inspect_schema


@dataclass(frozen=True)
class InputPreview:
    tables: list[tuple[str, int, int]]
    warnings: list[str]

    @property
    def total_rows(self):
        return sum(rows for _, rows, _ in self.tables)

    def row_budget(self, percent: int):
        # Match the benchmark's rounding, while displaying zero for an empty input.
        return max(1, int(self.total_rows * (percent / 100.0))) if self.total_rows else 0


def preview_input(config, kind: str, source: str) -> InputPreview:
    if kind == "mysql":
        engine = bounded_engine(replace(config.source_db, database=source).url, read_only=True)
        try:
            schema = inspect_schema(engine)
            return InputPreview(
                [
                    (name, table.row_count, len(table.columns))
                    for name, table in schema.tables.items()
                ],
                list(schema.unsupported_features),
            )
        finally:
            engine.dispose()
    if kind not in {"csv", "folder", "xlsx"}:
        raise ValueError("Choose a supported input type.")
    tables, total = [], 0
    with closing(iter_input_tables(Path(source))) as inputs:
        for label, headers, rows in inputs:
            columns = len(headers)
            if not columns:
                raise ValueError("Input table has no header columns.")
            cap(columns + 1, "max_columns")
            cap(len(tables) + 1, "max_tables")
            count = 0
            for index, values in enumerate(rows, 2):
                if not values:
                    continue
                if len(values) != columns:
                    raise ValueError(
                        f"{label}: row {index} has {len(values)} cells; expected {columns}."
                    )
                count += 1
                cap(total + count, "max_source_rows")
                for value in values:
                    if value is not None:
                        cap(len(str(value).encode("utf-8")), "max_cell_bytes")
            tables.append((label, count, columns))
            total += count
    return InputPreview(tables, [])
