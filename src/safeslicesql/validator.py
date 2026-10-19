"""
safeslicesql.validator — Subset validation and workload evaluation.

Validates generated subsets for:
- Primary-key uniqueness
- Foreign-key referential integrity
- Budget compliance
- Schema compatibility
- Source-database immutability

Also evaluates workload queries on both source and target databases,
computing per-query retention metrics.

Owner: M3
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine

from safeslicesql.types import ValidationReport, WorkloadReport
from safeslicesql.workload_analyzer import WorkloadAnalysis

logger = logging.getLogger("safeslicesql.validator")


def validate_subset(
    source_engine: Engine,
    target_engine: Engine,
    budget_limit: int,
) -> ValidationReport:
    """
    Validate a generated subset for correctness.

    Checks:
    - Primary-key uniqueness in the target.
    - No missing foreign-key references in the target.
    - Actual row count ≤ budget limit.
    - All expected tables present in the target.
    - Schema compatibility between source and target.
    - Source database unchanged (row counts match original).

    Parameters
    ----------
    source_engine : sqlalchemy.Engine
        Engine connected to the source database.
    target_engine : sqlalchemy.Engine
        Engine connected to the target (subset) database.
    budget_limit : int
        Maximum allowed total rows.

    Returns
    -------
    ValidationReport
        Detailed validation results.
    """
    raise NotImplementedError("Phase 6: implement subset validation")


def evaluate_workload(
    source_engine: Engine,
    target_engine: Engine,
    workload: WorkloadAnalysis,
) -> WorkloadReport:
    """
    Evaluate workload queries on source and target databases.

    For each supported query:
    1. Execute on source and record results.
    2. Execute on target and record results.
    3. Compute per-query metrics:
       - Query survival (nonempty source → nonempty target)
       - Result-row retention
       - Key retention
       - Aggregate retention
    4. Handle source-empty queries separately.
    5. Report unsupported queries with clear exclusion reasons.

    Ignores result ordering unless the query specifies ORDER BY.

    Parameters
    ----------
    source_engine : sqlalchemy.Engine
        Source database engine.
    target_engine : sqlalchemy.Engine
        Target (subset) database engine.
    workload : WorkloadAnalysis
        Parsed and analyzed workload.

    Returns
    -------
    WorkloadReport
        Per-query and aggregated workload evaluation results.
    """
    raise NotImplementedError("Phase 6: implement workload evaluation")
