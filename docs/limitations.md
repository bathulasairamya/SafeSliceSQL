# SafeSliceSQL — Limitations and Scope (v1)

## Supported Scope

| Category | Supported in v1 |
|---|---|
| **Database** | MySQL 8.x with InnoDB engine |
| **Primary Keys** | Single-column primary keys |
| **Foreign Keys** | Single-column foreign keys in acyclic graphs |
| **Nullable FKs** | Treated as "no dependency" — NULL FK values do not require parent row inclusion |
| **SQL Patterns** | SELECT, WHERE, JOIN (INNER), GROUP BY, basic aggregates (COUNT, SUM, MAX, MIN, AVG), HAVING |
| **Workload** | Fixed file of pre-defined queries |
| **Budget** | Hard row budget as percentage of total source rows |
| **Reproducibility** | Configurable random seeds |

## Not Supported in v1

| Category | Unsupported Feature | Handling |
|---|---|---|
| **Database** | Non-InnoDB engines, PostgreSQL, other RDBMS | Not tested; behavior undefined |
| **Primary Keys** | Composite primary keys | Detected and reported as unsupported |
| **Foreign Keys** | Composite foreign keys | Detected and reported as unsupported |
| **Foreign Keys** | Cyclic FK references | Detected and reported as unsupported |
| **SQL Patterns** | Subqueries, CTEs, UNION, INTERSECT, EXCEPT | Excluded with reason |
| **SQL Patterns** | Window functions (ROW_NUMBER, RANK, etc.) | Excluded with reason |
| **SQL Patterns** | INSERT, UPDATE, DELETE (DML) | Out of scope (read-only workloads) |
| **SQL Patterns** | DDL statements | Out of scope |
| **SQL Patterns** | Parameterized/dynamic queries | Not supported |
| **Joins** | LEFT/RIGHT/FULL OUTER JOIN | Not supported in v1 |
| **Budget** | Per-table budgets, storage-based budgets | Not implemented |
| **Budget** | Soft budgets | Not implemented |

## Nullable Foreign Key Policy

**Decision: NULL = no dependency.**

When a row has a NULL value in a foreign-key column:
- The row is eligible for selection.
- No parent row is required for the NULL FK reference.
- This aligns with standard SQL semantics where NULL FK means "no relationship."

## Known Limitations

1. **Greedy heuristic, not optimal.** The workload-aware selection uses a greedy algorithm. It does not guarantee a globally optimal subset.

2. **Fixed workload assumption.** Metrics only measure retention for the declared workload. The subset may not perform well for undeclared queries.

3. **Approximate row counts.** MySQL's `INFORMATION_SCHEMA.TABLES.TABLE_ROWS` provides estimates, not exact counts. Budget calculations use exact `COUNT(*)` queries.

4. **Single-database subsetting.** Cross-database foreign keys are not supported.

5. **No incremental updates.** Each run creates a fresh target database. There is no support for incrementally adding rows to an existing subset.

## Future Work

- Composite key support
- Cycle handling (e.g., via topological levels or deferred constraints)
- LEFT/RIGHT JOIN support in workload analysis
- Subquery and CTE support
- Incremental subset updates
- PostgreSQL support
- Storage-based budget option
