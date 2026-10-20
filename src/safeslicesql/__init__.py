"""
SafeSliceSQL — Workload-aware database subsetting with referential integrity.

This package provides tools to create smaller relational database subsets
while preserving referential integrity and retaining behavior for a
declared SQL workload.
"""

__version__ = "0.1.0"

from safeslicesql.config import AppConfig, load_config
from safeslicesql.dependency_graph import DependencyGraph, build_dependency_graph, compute_closure
from safeslicesql.sampler import select_rows
from safeslicesql.schema_analyzer import inspect_schema
from safeslicesql.subset_generator import materialize_subset
from safeslicesql.validator import evaluate_workload, validate_subset
from safeslicesql.workload_analyzer import analyze_workload

__all__ = [
    "__version__",
    "AppConfig",
    "load_config",
    "inspect_schema",
    "build_dependency_graph",
    "compute_closure",
    "DependencyGraph",
    "select_rows",
    "materialize_subset",
    "validate_subset",
    "analyze_workload",
    "evaluate_workload",
]
