# SafeSliceSQL Algorithm Design

## Implemented heuristic

Workload-aware selection uses static table weights derived from supported SQL
features. It ranks each candidate once by:

```
score(candidate) = table_weight / max(1, standalone_closure_size)
```

The standalone closure includes the candidate and all its required parents.
Scores are not recomputed as selection grows. This heuristic does not estimate
which individual rows satisfy predicates or the marginal query-result gain.

## Table weights

For each supported query without an analysis error, the implementation adds:

- 2.0 to every referenced table.
- 0.5 per detected join pair to the first referenced table.
- 1.0 per detected filter column to that column's table.
- 1.5 per detected grouping column to that column's table.
- 1.0 to the first referenced table when grouping or aggregation is present.

An otherwise unweighted table receives weight 1.0. Workload-aware selection
requires at least one usable supported query; missing or unusable workloads
are errors rather than a silent fallback to uniform weights.

## Selection procedure

1. Sample candidates from each table using a seeded reservoir over primary-key
   order. The proportional pool targets 1.25 times the budget, capped by source
   size; per-table rounding and a one-candidate minimum can increase this total.
2. Prefetch candidate foreign-key values and compute each candidate's closure.
3. Calculate the static score, shuffle with the recorded seed, then stably sort
   scores descending. The seeded shuffle resolves equal-score ties.
4. Visit candidates in that order. Skip rows already selected. Compute the
   incremental number of closure rows not yet in the selection.
5. Accept the candidate and dependencies only if that incremental cost fits
   the hard row budget. Reject candidates that do not fit.
6. Stop when the budget is full or all sampled candidates have been considered.

Incremental feasibility is recalculated at acceptance time; rankings and table
weights remain fixed. A rejected candidate is not revisited, so the result may
leave unused budget and has no optimality guarantee.

## Baselines

Random-repair shuffles the sampled candidates and applies the same closure and
budget checks. Referential selection visits tables in parent-first order and
shuffles candidates within each table. Neither baseline uses workload weights.

## Primary keys and reproducibility

Single-column primary keys use scalar values. Composite primary keys use tuples
in schema order. Live sampling orders by every primary-key column, so a fixed
seed is independent of the database's unordered scan plan on unchanged data.
Composite foreign-key constraints remain unsupported.

## Resource use

Candidate memory scales with the sampled pool and dependency closures, rather
than all source primary keys. Sampling still scans each table's keys; ordering
can require database work when an index cannot satisfy the ordering. Scoring
sorts C candidates in O(C log C), in addition to traversal and database costs.

Workload analysis streams result rows to count them. Evaluation streams both
results into a temporary indexed SQLite store for exact multiset and distinct-key
comparisons. Python memory is bounded by a batch and individual row size;
temporary disk use scales with distinct result, key, and group tuples. The store
is closed and removed after each query, including failures.
