# SafeSliceSQL Algorithm Design

## Implemented heuristic

Workload-aware selection first obtains bounded primary-key witnesses using the
actual FROM/JOIN/WHERE conditions of each supported query. It visits witnesses
round-robin across queries, adding their required dependencies only when they fit.
Remaining sampled candidates are ranked by static table weights. Incremental
closure traversal skips previously selected dependencies and stops once the
remaining budget is exceeded. No standalone closure is computed for scoring.
HAVING thresholds and optimal marginal query-result gain are not solved; the
actual workload is evaluated before committing the target.

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

1. Seed actual workload witnesses as described above, then sample candidates
   from each table. The proportional pool targets 1.25 times the budget, capped by source
   size; per-table rounding and a one-candidate minimum can increase this total.
2. Prefetch candidate foreign-key values; cache referenced unique-key lookups.
3. Use the table weight as a static score, shuffle with the recorded seed, then stably sort
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
than all source primary keys. Dense integer keys use uniform rejection sampling
with bounded indexed lookups. Sparse, textual and composite keys retain an exact
streaming reservoir scan. Ordering can require database work when an index
cannot satisfy the ordering. Scoring
sorts C candidates in O(C log C), in addition to traversal and database costs.

Standalone workload analysis can stream result rows to count them; pipeline
setup skips that redundant execution. Evaluation streams both
results into a temporary indexed SQLite store for exact multiset and distinct-key
comparisons. Python memory is bounded by a batch and individual row size;
temporary disk use scales with distinct result, key, and group tuples. The store
is closed and removed after each query, including failures.
