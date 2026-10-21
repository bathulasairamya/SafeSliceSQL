# SafeSliceSQL — Algorithm Design

## Overview

SafeSliceSQL uses a budget-aware, workload-aware greedy selection heuristic
that values candidate rows by their marginal contribution to the declared
workload while accounting for the cost of including required dependencies.

## Score Function

```
score(c | S) = ΔV(c | S) / max(1, ΔD(c | S))
```

Where:
- `ΔV(c | S)` = marginal workload-retention value of candidate `c`
  given current selection `S`
- `ΔD(c | S)` = number of additional rows needed by dependency closure

## Procedure (Pseudocode)

```
1. Initialize selection S ← ∅
2. Generate feasible candidate rows
3. For each candidate c:
   a. Estimate ΔV(c | S) — marginal workload value
   b. Compute ΔD(c | S) — dependency closure cost
   c. Compute score(c | S)
4. Choose best feasible candidate (documented tie-breaking)
5. Add candidate + dependencies to S
6. Update marginal-value calculations
7. Repeat until no useful candidate fits remaining budget
8. Materialize S
```

## Primary-Key Representation

Single-column primary keys use their scalar value. Composite primary keys use
tuples in schema order, such as `(emp_no, dept_no)`. Candidate selection,
dependency closure, validation, and materialization share this representation.
Composite foreign-key constraints remain unsupported.

## Candidate Pool and Runtime

Selectors use deterministic reservoir sampling to cap candidates near the row
budget. Foreign-key values for sampled candidates are prefetched in batches,
and candidate closures are merged incrementally under the hard budget. This
avoids loading every primary key into memory and repeatedly recomputing the
entire selected closure.

## Value Function Features

*To be defined based on workload analysis:*
- Join participation
- Filter relevance
- Aggregate/grouping contribution
- Query coverage (nonempty result contribution)

## Complexity

*To be measured after implementation.*
