# SafeSliceSQL Metric Definitions

## Workload accounting

Supported syntax and execution success are separate. Unsupported queries (including subqueries, CTEs, set operations, outer joins, and window functions) are excluded with a reason. Supported queries that fail on either database remain supported, carry an error, and receive zero retention scores. Runs containing workload execution errors are unsuccessful and are excluded from benchmark performance averages.

Query survival is the number of supported queries returning at least one row on both databases divided by the eligible source queries. Confirmed source-empty queries are excluded from this denominator. If source execution fails and no successful baseline establishes whether the source was empty, that query is conservatively included as a failed eligible query. Reports expose the denominator, known source-nonempty count, unknown-source count, and failed-query count.

## Retention

- **Result-row retention:** multiset intersection size divided by the number of source result rows. Duplicate tuples count up to their multiplicity in each result. All columns are preserved by ordinal position, including repeated output names. Values are compared with their driver-returned types; result ordering is ignored.
- **Key retention:** fraction of distinct source key tuples retained, using declared primary keys mapped through simple projections and aliases, or explicit projected grouping keys. Ambiguous projections fall back to result-row retention. Column-name suffixes do not identify keys.
- **Aggregate retention:** survival of distinct grouping-key combinations when every grouping expression can be mapped to a projected output position. Mapping handles column aliases and GROUP BY ordinals. It measures group survival, not numerical aggregate accuracy. For scalar aggregates or grouping expressions that cannot be mapped safely, the metric conservatively uses exact result-row retention; missing output columns never become artificial `None` group keys.
- **Empty/empty results:** retention scores are 1, while query survival is false and the query is excluded from the survival denominator. Retention averages include all supported queries, including these empty cases and execution failures (zero).

## Integrity and source verification

Validation checks table/column definitions, primary/foreign keys, unique/check constraints, actual target row count, and referential integrity. Required checks that cannot execute fail validation.

CLI runs and benchmarks capture a streaming content fingerprint during setup.
Each pipeline establishes a repeatable-read source transaction and verifies its
fingerprint against that baseline before target changes. Selection, copying and
evaluation use the same read view; final validation reuses that verified
fingerprint. It includes schema definitions, values, types and row multiplicities.
Source fingerprinting still requires full scans.

Standalone validation without a before-run content snapshot reports source immutability as **NOT VERIFIED** (`source_unchanged=None`). A row-count baseline can prove a change but cannot prove unchanged content. Structural validity can still pass with source history unverified. API callers can use `capture_source_snapshot(engine)` and pass the result as `initial_source_snapshot` to `validate_subset`.

Standalone fingerprints compare endpoint states; they do not prove absence of
intervening writes. Pipeline results describe the stable source read view, not
the absence of concurrent external writes. Avoid concurrent schema changes.

## Benchmark summaries

Raw results retain every attempted run and its failure details. Performance means and sample standard deviations use successful runs only; absent workload measurements remain missing rather than zero. Summaries include attempted/successful/failed counts and observation counts per metric. Groups with no successful measurements show N/A, and single-observation standard deviations are N/A. Charts omit missing/failed measurements rather than substituting zero.

Generated observations describe the measured values and ties. They do not assert predetermined winners or statistical superiority. Historical reports generated with older metric definitions must be regenerated before comparison with current results.

## Timing

Selection, materialization, validation, and workload evaluation have separate timings.
Initial baseline capture and source snapshot preflight are setup work outside
those stage timings. Materialization time excludes its nested evaluation and
validation steps to avoid double counting. Failed runs do not contribute
artificially short timings to successful-run means.

## Large query results

Analysis counts streamed result rows without retaining them. Evaluation uses
streamed reads and a temporary disk-backed index for exact row multiplicities,
keys, and grouping combinations. It needs temporary disk space proportional to
distinct result data. Query timings include execution and streaming; comparison
ingestion is recorded separately.
Temporary data is removed on success and failure; exhaustion of disk space is
reported as an evaluation failure, not silently truncated results.
## Timing and identity clarification

Per-query `execution_time_source` and `execution_time_target` measure connection,
execution and result-fetch time on the client. They are not isolated server CPU
time. JSON encoding and temporary comparison-store insertion are recorded
separately as `comparison_time_source` and `comparison_time_target`.

Key retention uses reflected primary keys mapped through simple projections,
including aliases. Explicit projected GROUP BY keys identify aggregate groups.
Ambiguous projections fall back to exact result-row retention; column-name
suffixes are not used to guess identities.
