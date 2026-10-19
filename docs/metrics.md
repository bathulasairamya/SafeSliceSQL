# SafeSliceSQL — Metric Definitions

*To be fully implemented in Phase 6.*

## Workload Metrics

| Metric | Definition | Notes |
|---|---|---|
| **Query survival** | Fraction of queries nonempty on source that remain nonempty in subset | Excludes source-empty queries from denominator |
| **Result-row retention** | Defined comparison of source and subset result rows | Uses key/tuple normalization where needed |
| **Key retention** | Fraction of relevant source keys retained in subset results | For queries with meaningful key-based results |
| **Aggregate retention** | Comparison of aggregate/group outputs | Uses declared matching rule and tolerance |

## Integrity Metrics

| Metric | Definition | Expected |
|---|---|---|
| **Referential violations** | Count of broken FK references in target | Must be 0 |
| **PK uniqueness** | All PKs unique in target | Must be True |

## Size Metrics

| Metric | Definition | Constraint |
|---|---|---|
| **Row reduction** | `1 - subset_rows / source_rows` | — |
| **Budget deviation** | `actual_rows - budget_limit` | Must be ≤ 0 (hard budget) |
| **Storage reduction** | Optional disk-size comparison | Use consistent MySQL procedure |

## Timing Metrics

| Metric | Measured Separately |
|---|---|
| Selection time | Time to run row selection algorithm |
| Materialization time | Time to create and populate target DB |
| Validation time | Time to check PK/FK/budget |
| Workload evaluation time | Time to run queries on source and target |

## Edge Cases

- **Source-empty queries:** Excluded from query-survival denominator; reported separately
- **Ordering:** Ignored unless query has explicit ORDER BY
- **Aggregates:** Group matching uses documented rules
