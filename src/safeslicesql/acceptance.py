"""Explicit workload quality gates, separate from structural integrity."""

import math
from dataclasses import dataclass, field

from safeslicesql.types import WorkloadReport


@dataclass(frozen=True)
class AcceptancePolicy:
    min_query_survival: float = 0.0
    min_result_retention: float = 0.0
    required_queries: tuple[str, ...] = ()

    def __post_init__(self):
        for value in (self.min_query_survival, self.min_result_retention):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("Acceptance thresholds must be numbers from 0 to 1.")
        if not isinstance(self.required_queries, (tuple, list)) or any(
            not isinstance(query, str) or not query for query in self.required_queries
        ):
            raise ValueError("required_queries must contain nonempty query IDs.")

    def evaluate(self, report: WorkloadReport) -> "AcceptanceReport":
        errors = []
        if report.failed_queries:
            errors.append("Workload execution contains failures.")
        if report.query_survival_rate < self.min_query_survival:
            errors.append(
                f"Query survival {report.query_survival_rate:.1%} is below {self.min_query_survival:.1%}."
            )
        if self.min_query_survival > 0 and not report.survival_denominator:
            errors.append("No eligible source queries were available to demonstrate survival.")
        if report.overall_result_retention < self.min_result_retention:
            errors.append(
                f"Result retention {report.overall_result_retention:.1%} is below {self.min_result_retention:.1%}."
            )
        by_id = {query.query_id: query for query in report.per_query_results}
        for query_id in self.required_queries:
            query = by_id.get(query_id)
            if query is None or query.excluded or query.error or not query.survived:
                errors.append(
                    f"Required query {query_id} did not survive or could not be evaluated."
                )
        return AcceptanceReport(not errors, errors)


@dataclass
class AcceptanceReport:
    passed: bool
    errors: list[str] = field(default_factory=list)
