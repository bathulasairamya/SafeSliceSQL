"""
safeslicesql.types — Shared data structures for SafeSliceSQL.

All modules use these types as their interface contracts. Changes to
these types should be reviewed by the full team since they affect
every component in the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ────────────────────────────────────────────────────────────
# Schema metadata types
# ────────────────────────────────────────────────────────────

@dataclass
class ColumnMetadata:
    """Metadata for a single table column."""

    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool = False
    column_default: str | None = None
    extra: str = ""  # e.g. "auto_increment"


@dataclass
class ForeignKeyMetadata:
    """Metadata for a single foreign-key constraint."""

    constraint_name: str
    column: str                  # Referencing (child) column
    referenced_table: str        # Referenced (parent) table
    referenced_column: str       # Referenced (parent) column


@dataclass
class TableMetadata:
    """Metadata for a single database table."""

    name: str
    columns: list[ColumnMetadata] = field(default_factory=list)
    primary_key: str | None = None          # Single-column PK column name
    row_count: int = 0
    foreign_keys: list[ForeignKeyMetadata] = field(default_factory=list)

    @property
    def column_names(self) -> list[str]:
        """Return a list of column names."""
        return [c.name for c in self.columns]

    def get_column(self, name: str) -> ColumnMetadata | None:
        """Retrieve column metadata by name."""
        for col in self.columns:
            if col.name == name:
                return col
        return None


@dataclass
class SchemaMetadata:
    """Complete schema metadata for a database."""

    database_name: str
    tables: dict[str, TableMetadata] = field(default_factory=dict)
    unsupported_features: list[str] = field(default_factory=list)

    @property
    def total_row_count(self) -> int:
        """Total row count across all tables."""
        return sum(t.row_count for t in self.tables.values())

    @property
    def table_names(self) -> list[str]:
        """Sorted list of table names."""
        return sorted(self.tables.keys())


# ────────────────────────────────────────────────────────────
# Dependency graph types
# ────────────────────────────────────────────────────────────

@dataclass
class ClosureResult:
    """Result of computing the dependency closure for a set of rows."""

    selected_rows: dict[str, set[Any]]    # table → set of PK values
    closure_additions: dict[str, set[Any]]  # additional parent rows added
    total_rows: int = 0
    total_closure_additions: int = 0
    is_feasible: bool = True              # False if closure exceeds budget
    infeasibility_reason: str = ""


# ────────────────────────────────────────────────────────────
# Selection types
# ────────────────────────────────────────────────────────────

@dataclass
class SelectionResult:
    """Result of a row-selection method."""

    method: str
    seed: int
    budget: int                             # Max allowed total rows
    selected_rows: dict[str, list[Any]]     # table_name → list of PK values
    total_selected: int = 0
    closure_additions: int = 0
    rejected_candidates: int = 0
    selection_log: list[str] = field(default_factory=list)


# ────────────────────────────────────────────────────────────
# Materialization types
# ────────────────────────────────────────────────────────────

@dataclass
class GenerationResult:
    """Result of materializing a subset into the target database."""

    target_database: str
    tables_created: list[str] = field(default_factory=list)
    rows_inserted: dict[str, int] = field(default_factory=dict)
    total_rows_inserted: int = 0
    insertion_order: list[str] = field(default_factory=list)
    success: bool = False
    error_message: str = ""


# ────────────────────────────────────────────────────────────
# Validation types
# ────────────────────────────────────────────────────────────

@dataclass
class ValidationReport:
    """Result of validating a generated subset."""

    pk_unique: bool = False
    fk_violations: int = 0
    fk_violation_details: list[str] = field(default_factory=list)
    budget_compliant: bool = False
    actual_rows: int = 0
    budget_limit: int = 0
    tables_present: list[str] = field(default_factory=list)
    tables_missing: list[str] = field(default_factory=list)
    source_unchanged: bool = False
    schema_compatible: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Overall validity: all checks must pass."""
        return (
            self.pk_unique
            and self.fk_violations == 0
            and self.budget_compliant
            and self.source_unchanged
            and self.schema_compatible
            and len(self.tables_missing) == 0
            and len(self.errors) == 0
        )


# ────────────────────────────────────────────────────────────
# Workload evaluation types
# ────────────────────────────────────────────────────────────

@dataclass
class QueryResult:
    """Per-query evaluation result comparing source vs target."""

    query_id: str
    query_text: str
    source_row_count: int = 0
    target_row_count: int = 0
    source_empty: bool = False
    target_empty: bool = False
    survived: bool = False           # Nonempty on source and target
    key_retention: float = 0.0       # Fraction of source keys retained
    result_row_retention: float = 0.0
    aggregate_retention: float = 0.0
    execution_time_source: float = 0.0
    execution_time_target: float = 0.0
    error: str = ""
    excluded: bool = False           # True if query pattern is unsupported
    exclusion_reason: str = ""


@dataclass
class WorkloadReport:
    """Aggregated workload evaluation report."""

    per_query_results: list[QueryResult] = field(default_factory=list)
    total_queries: int = 0
    supported_queries: int = 0
    excluded_queries: int = 0
    source_nonempty_queries: int = 0
    query_survival_rate: float = 0.0
    overall_key_retention: float = 0.0
    overall_result_retention: float = 0.0
    overall_aggregate_retention: float = 0.0


# ────────────────────────────────────────────────────────────
# Benchmark types
# ────────────────────────────────────────────────────────────

@dataclass
class BenchmarkRunResult:
    """Result of a single benchmark run."""

    run_id: str
    method: str
    seed: int
    budget_percent: float
    dataset_size: int
    selection: SelectionResult | None = None
    generation: GenerationResult | None = None
    validation: ValidationReport | None = None
    workload: WorkloadReport | None = None
    runtime_selection: float = 0.0
    runtime_materialization: float = 0.0
    runtime_validation: float = 0.0
    runtime_workload_eval: float = 0.0
    success: bool = False
    error_message: str = ""


@dataclass
class BenchmarkResults:
    """Aggregated results from a full benchmark matrix."""

    runs: list[BenchmarkRunResult] = field(default_factory=list)
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    config_path: str = ""
