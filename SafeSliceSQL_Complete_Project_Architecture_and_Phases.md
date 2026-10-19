## Phase-Wise Implementation Plan

The project is organized by **phases and exit criteria**, not by weeks or fixed calendar days. Complete each phase's deliverables before treating it as finished. With an 18-day deadline, use the phase order to prioritize work and overlap independent tasks where practical.

### Phase 1 — Scope, Setup, and Project Skeleton
**Goal:** Establish a runnable foundation and a shared definition of the project.

**Tasks**
- Create the `SafeSliceSQL` repository and the package structure.
- Set up Python, MySQL, the virtual environment, dependencies, `.env.example`, and `.gitignore`.
- Select the initial supported scope: MySQL/InnoDB, single-column primary and foreign keys, and an explicitly documented policy for nullable foreign keys.
- Define the workload SQL file, configuration format, logging conventions, and module interfaces.
- Load the Employees sample database and confirm the team can connect to it.

**Deliverables**
- Repository skeleton and setup instructions.
- Working database connection and sample workload.
- Written scope, assumptions, and limitations.

**Exit criteria**
- Every team member can set up the project and run the sample workload from a clean environment.

### Phase 2 — Schema Analysis and Dependency Graph
**Goal:** Understand the source database and its referential dependencies.

**Tasks**
- Introspect tables, columns, primary keys, foreign keys, nullability, and row counts.
- Build a directed dependency graph, consistently using referenced parent → referencing child edges.
- Implement recursive dependency closure: selected child rows bring in required referenced parent rows.
- Detect unsupported composite keys, cycles, and schema features outside the supported scope; report them clearly rather than silently mishandling them.
- Add unit tests for graph construction and closure.

**Deliverables**
- Schema metadata representation.
- Foreign-key dependency graph and closure implementation.
- Tests for parent inclusion and unsupported-schema detection.

**Exit criteria**
- Given selected rows, closure correctly identifies required parent rows for supported schemas.

### Phase 3 — Subset Materialization and Integrity
**Goal:** Generate a smaller database without modifying the source.

**Tasks**
- Create a separate target database with the supported schema.
- Insert selected rows while preserving original primary-key values and respecting parent-before-child ordering.
- Implement transactions and safe target-database naming/refresh safeguards.
- Enforce a hard total-row budget: reject additions whose dependency closure would exceed the budget.
- Validate primary-key uniqueness and foreign-key integrity; do not use disabled foreign-key checks as a workaround.

**Deliverables**
- Subset materializer.
- Budget accounting and insertion-order logic.
- Integrity checks and tests.

**Exit criteria**
- A generated subset is smaller than or equal to the configured row budget, has zero foreign-key violations, and leaves the source unchanged.

### Phase 4 — Baseline Sampling Methods
**Goal:** Build fair comparison methods before evaluating the proposed algorithm.

**Tasks**
- Implement random sampling with dependency repair.
- Implement referential/table-aware sampling with dependency closure.
- Make random seeds configurable and results reproducible.
- Ensure all methods use the same schema scope, workload, row-budget definition, and materialization pipeline.

**Deliverables**
- Two baseline selectors.
- Shared method interface and reproducibility tests.
- Baseline run logs.

**Exit criteria**
- Both baselines generate valid subsets under the same budget and can be rerun with controlled seeds.

### Phase 5 — SafeSliceSQL Workload-Aware Selection
**Goal:** Prefer rows likely to preserve useful behavior for the declared SQL workload.

**Tasks**
- Parse and document the supported workload query patterns.
- Extract workload features such as join participation, filter relevance, and aggregate/grouping relevance.
- Define a transparent marginal value function and a dependency-closure cost.
- Implement a greedy selector using a score such as:

  `score(candidate | selected) = marginal workload value / max(1, additional rows required)`

- Recompute incremental costs based on rows already selected; skip candidates that violate the hard budget.
- Log selection decisions and make the heuristic's limitations explicit.

**Deliverables**
- Workload analyzer for the supported query subset.
- Greedy selection algorithm and decision logs.
- Unit tests and a small end-to-end demonstration.

**Exit criteria**
- SafeSliceSQL runs reproducibly, respects the budget and integrity constraints, and produces measurable workload metrics. Any advantage over baselines must be demonstrated by experiments, not assumed.

### Phase 6 — Validation and Workload Evaluation
**Goal:** Measure correctness and behavior retention consistently.

**Tasks**
- Check schema compatibility, primary-key uniqueness, foreign-key violations, and budget compliance.
- Execute the declared workload on source and target databases.
- Measure query survival, result-row or key retention where meaningful, and aggregate/group retention using documented comparison rules.
- Report source-empty queries separately; do not treat them as evidence of preservation.
- Ignore result ordering unless `ORDER BY` is specified; use normalized or multiset comparisons where appropriate.
- Record selection, materialization, validation, and query-evaluation runtimes separately.

**Deliverables**
- Structured validation and workload reports.
- Metric definitions and automated tests.
- Clear handling of unsupported SQL patterns.

**Exit criteria**
- Each method is evaluated by the same metric implementation, and every reported metric can be traced to recorded output.

### Phase 7 — CLI Integration and End-to-End Workflow
**Goal:** Make the project usable by someone other than its original author.

**Tasks**
- Integrate schema inspection, method selection, subset generation, validation, and workload evaluation.
- Implement and document consistent CLI commands, for example:

  ```bash
  safeslice inspect --database employees_source
  safeslice run --method random-repair --budget-percent 20 --seed 42
  safeslice run --method referential --budget-percent 20 --seed 42
  safeslice run --method workload-aware --budget-percent 20 --seed 42
  safeslice validate --source employees_source --target employees_slice
  safeslice benchmark --config benchmarks/benchmarks.yaml
  ```

- Add actionable errors, logs, configuration validation, and safe handling of target database names.
- Test the complete workflow from a clean setup.

**Deliverables**
- Integrated CLI and configuration.
- README setup/run instructions.
- End-to-end smoke test.

**Exit criteria**
- Another team member can execute the documented workflow without manual intervention from the primary developer.

### Phase 8 — Benchmarking and Experimental Analysis
**Goal:** Compare methods fairly and produce defensible evidence.

**Tasks**
- Start with the Employees database for debugging, then evaluate on Sakila and controlled synthetic datasets if feasible.
- Test several dataset sizes, row budgets, and random seeds.
- Suggested starting grid: 10K, 50K, and 100K total rows; 10%, 20%, and 40% budgets; three seeds; all three methods. This is a target, not a requirement to run configurations that exceed available time.
- Run a pilot first, then reduce the grid transparently if runtime is too high.
- Save raw results to CSV and generate plots from those files.
- Optionally run an ablation without the workload-value term if time permits.
- Document machine/software environment and any failed or skipped runs.

**Deliverables**
- Benchmark configuration and raw CSV results.
- Reproducible plotting scripts.
- Comparison charts and an experimental analysis.

**Exit criteria**
- Results are reproducible, comparisons use a common protocol, and no performance or quality claims are made without supporting data.

### Phase 9 — Finalization, Documentation, and Defense
**Goal:** Present a complete, understandable, and reproducible academic project.

**Tasks**
- Finish unit, integration, and end-to-end tests.
- Document architecture, supported SQL patterns, algorithm pseudocode, metrics, results, limitations, and future work.
- Prepare architecture and workflow diagrams.
- Rehearse a concise demonstration: inspect schema → run a baseline → run SafeSliceSQL → validate target → compare actual results.
- Prepare viva answers on dependency closure, hard budgets, greedy heuristics, workload assumptions, fairness of baselines, and unsupported schema/query cases.

**Deliverables**
- Final README and technical report.
- Architecture/workflow diagrams and actual benchmark plots.
- Reproducible demo commands and viva notes.

**Exit criteria**
- The project runs from documented instructions, the demo uses real outputs, and limitations are stated accurately.

### Team Ownership

- **Member 1 — Database and dependencies:** `db.py`, `schema_analyzer.py`, `dependency_graph.py`, `subset_generator.py`.
- **Member 2 — Workload and selection:** `workload_analyzer.py`, `sampler.py`, baseline methods, and the SafeSliceSQL greedy selector.
- **Member 3 — Validation and integration:** `validator.py`, `benchmark.py`, `cli.py`, test integration, plots, and report integration.

Agree on interfaces during Phase 1. Integrate continuously rather than waiting until the end, and ensure all three members understand the full pipeline.

### Recommended Phase Order

`Setup → Schema & Dependencies → Materialization & Integrity → Baselines → SafeSliceSQL Algorithm → Validation → CLI Integration → Benchmarks → Final Defense`

Phases may overlap when their interfaces are stable, but correctness and baseline comparisons should be established before interpreting the proposed algorithm's results.


## 1. Executive Summary

SafeSliceSQL creates smaller relational database subsets while preserving
referential integrity and retaining as much behavior as possible for a
declared SQL workload. It compares a proposed workload-aware selection
method against baseline sampling strategies under a defined row budget.

The project is not intended to prove equivalence for arbitrary SQL. It
supports a documented subset of SQL patterns, measures workload behavior
using explicit metrics, and reports unsupported cases honestly.

### Core input

-   A source MySQL database.
-   A fixed workload of supported `SELECT` queries.
-   A row budget, such as 10%, 20%, or 40% of source rows.
-   A selection method and reproducibility seed.

### Core output

-   A separate target MySQL database containing the selected subset.
-   A schema and dependency report.
-   Referential-integrity validation.
-   Per-query workload-retention measurements.
-   Runtime, row counts, budget adherence, and optional storage
    measurements.
-   Raw CSV results and reproducible benchmark plots.

### Main contribution

A budget-aware, workload-aware greedy selection heuristic that values
candidate rows by their marginal contribution to the declared workload
while accounting for the cost of including required dependencies.

------------------------------------------------------------------------

## 2. Problem Statement

Traditional random sampling may remove rows that are important to joins,
filters, or aggregate queries. Foreign-key validity alone does not
guarantee that a subset preserves the behavior of a workload.

SafeSliceSQL addresses the following problem:

> Given a relational database, a supported SQL workload, and a strict
> row budget, select a smaller subset that satisfies supported
> referential constraints and maximizes defined workload-retention
> objectives as effectively as possible.

### Scope and guarantees

**The system aims to guarantee, for supported schemas and operations:**

1.  The source database is not modified.
2.  Primary-key values are preserved for selected rows.
3.  Supported foreign-key constraints remain valid.
4.  The configured hard row budget is not silently exceeded.
5.  Each benchmark records its method, configuration, seed, and measured
    outcomes.
6.  Unsupported schema features and query patterns are reported rather
    than silently ignored.

**The system does not claim:**

-   Equivalence for arbitrary SQL.
-   A globally optimal subset from a greedy heuristic.
-   That the proposed method wins every dataset, query workload, or
    budget.
-   Support for every MySQL feature in the first version.

------------------------------------------------------------------------

## 3. Project Objectives

1.  Introspect a live MySQL schema automatically.
2.  Represent supported foreign-key relationships as a dependency graph.
3.  Calculate the dependency closure required by selected seed rows.
4.  Generate an isolated target database while preserving supported
    constraints.
5.  Implement two fair baseline methods.
6.  Implement a workload-aware, budget-aware selection heuristic.
7.  Compare source and target workload behavior using explicit metrics.
8.  Run reproducible experiments at multiple dataset sizes and budgets.
9.  Provide a CLI, automated tests, CSV reports, plots, and
    documentation.
10. Deliver a clear live demonstration and defend the design in a viva.

------------------------------------------------------------------------

## 4. High-level System Architecture

``` mermaid
flowchart TD
    A[Source MySQL Database] --> B[Schema Analyzer]
    W[SQL Workload File] --> C[Workload Analyzer]
    B --> D[Foreign-Key Dependency Graph]
    C --> E[Query Features and Baseline Results]
    D --> F[Selection Engine]
    E --> F
    F --> G{Selection Method}
    G --> H1[Random Sampling + Repair]
    G --> H2[Referential Sampling]
    G --> H3[SafeSliceSQL Workload-aware Greedy]
    H1 --> I[Dependency Closure and Budget Check]
    H2 --> I
    H3 --> I
    I --> J[Subset Generator]
    J --> K[Separate Target MySQL Database]
    A --> L[Validation and Workload Evaluation]
    K --> L
    L --> M[CLI Summary and Logs]
    L --> N[Raw CSV Results]
    N --> O[Plots and Analysis]
    O --> P[Report and Presentation]
```

### Component responsibilities

  -----------------------------------------------------------------------
  Component                           Responsibility
  ----------------------------------- -----------------------------------
  Source database                     Provides original schema and rows;
                                      remains unchanged

  Schema analyzer                     Discovers tables, columns, primary
                                      keys, foreign keys, nullability,
                                      and supported features

  Dependency graph                    Represents foreign-key parent-child
                                      relationships and detects
                                      unsupported cycles

  Workload analyzer                   Reads the declared query workload
                                      and extracts supported query
                                      features

  Selection engine                    Runs a baseline or the proposed
                                      workload-aware method

  Dependency closure                  Adds required referenced parent
                                      rows

  Budget manager                      Rejects candidates that cannot fit
                                      within the hard budget

  Subset generator                    Creates and populates the separate
                                      target database

  Validator                           Checks primary-key and foreign-key
                                      integrity and budget adherence

  Workload evaluator                  Runs comparable queries on source
                                      and target and records defined
                                      metrics

  Benchmark runner                    Repeats configurations across
                                      methods, budgets, sizes, and seeds

  CLI and reporting                   Provides commands, logs, CSVs,
                                      plots, and reproducibility metadata
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 5. End-to-end Execution Workflow

### Step 1 --- Configure a run

The user specifies:

-   Source database.
-   Target database or target naming pattern.
-   Workload file.
-   Selection method.
-   Budget percentage or absolute row limit.
-   Random seed.
-   Output directory and benchmark configuration.

### Step 2 --- Inspect the source schema

The schema analyzer retrieves metadata from MySQL. It checks for
unsupported features before selection begins.

Output: normalized schema metadata, table counts, primary keys, foreign
keys, and feature warnings.

### Step 3 --- Analyze the SQL workload

The workload analyzer loads the fixed workload and identifies supported
patterns, tables, join conditions, filters, grouping, and aggregate
functions. It runs each query against the source and records the source
result or a normalized comparison representation.

Unsupported queries must produce a clear error or an explicitly excluded
status with a reason. They must not silently contribute fabricated
metrics.

### Step 4 --- Build the dependency graph

Represent foreign-key relationships as edges from referenced parent
table to referencing child table, while documenting this direction
consistently. For a selected child row, the engine must find the parent
row it references.

The initial version supports single-column primary/foreign keys and
acyclic foreign-key graphs. Nullable foreign-key behavior is defined
explicitly. Composite keys and cycles are detected and reported as
unsupported until implemented and tested.

### Step 5 --- Generate candidate selections

Each method proposes seed rows or candidate groups. Candidates must be
evaluated with their dependency closure, not only by the number of seed
rows.

### Step 6 --- Apply dependency closure

For each candidate:

1.  Inspect its foreign-key values.
2.  Find referenced parent rows missing from the current selection.
3.  Add the required rows.
4.  Continue until no additional supported dependencies are required.
5.  Compute the marginal row cost.
6.  Reject candidates that exceed the remaining hard budget.

The closure operation must be deterministic for a fixed input and seed,
where the method's design permits it.

### Step 7 --- Materialize the subset

Create a fresh target schema. Reproduce supported table definitions,
insert selected parent rows before child rows, preserve key values, and
use transactions where appropriate. Do not disable foreign-key checks as
a substitute for a correct dependency strategy.

### Step 8 --- Validate the subset

Check:

-   Primary-key uniqueness.
-   Missing foreign-key references.
-   Actual row count and budget compliance.
-   Expected table presence and schema compatibility.
-   Successful target queries.
-   Source database unchanged.
-   Clear handling of unsupported features.

### Step 9 --- Evaluate the workload

Run the same supported workload against source and target. Compare
results using metrics appropriate to each query type. Record per-query
outcomes, not just one aggregate score.

### Step 10 --- Record and report results

Save configuration, seed, method, code version if available, row counts,
runtime, integrity results, query metrics, warnings, and artifact paths.
Generate plots from raw records, not manually entered values.

------------------------------------------------------------------------

## 6. Recommended Repository Structure

``` text
SafeSliceSQL/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── sql/
│   ├── schema.sql
│   └── seed_data.sql
├── workloads/
│   └── workload.sql
├── benchmarks/
│   └── benchmarks.yaml
├── src/
│   └── safesliceql/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── db.py
│       ├── schema_analyzer.py
│       ├── dependency_graph.py
│       ├── workload_analyzer.py
│       ├── sampler.py
│       ├── subset_generator.py
│       ├── validator.py
│       ├── benchmark.py
│       └── reporting.py
├── tests/
│   ├── test_schema.py
│   ├── test_dependencies.py
│   ├── test_sampler.py
│   ├── test_subset_generator.py
│   ├── test_validator.py
│   └── test_workload.py
├── scripts/
│   ├── generate_data.py
│   └── plot_results.py
├── docs/
│   ├── architecture.md
│   ├── algorithm.md
│   ├── metrics.md
│   ├── limitations.md
│   └── demo_script.md
└── results/
    ├── raw/
    ├── summaries/
    └── plots/
```

`results/` may be excluded from Git for large generated outputs; commit
a small, representative reproducibility sample if appropriate. Never
commit credentials, `.env`, or private database dumps.

------------------------------------------------------------------------

## 7. Technology and Environment Setup

### Requirements

-   Python 3.11 or newer.
-   MySQL 8.x using InnoDB.
-   MySQL Workbench or another SQL client.
-   Git and VS Code.
-   Enough disk space for source, target, and synthetic benchmark
    datasets.

### Python environment

``` bash
python -m venv .venv
```

Activate on Windows PowerShell:

``` powershell
.\.venv\Scripts\Activate.ps1
```

Activate on Linux or macOS:

``` bash
source .venv/bin/activate
```

Install the initial dependencies:

``` bash
python -m pip install --upgrade pip
pip install sqlalchemy pymysql networkx typer pytest pandas matplotlib python-dotenv
```

Configure the package in `pyproject.toml` before running
`pip install -e .`. Expose a CLI entry point named `safeslice` once the
package is configured.

### Configuration and security

-   Keep credentials in an ignored `.env` file.
-   Provide `.env.example` with placeholder variable names only.
-   Use a read-only account for source inspection where possible.
-   Use a separate account or appropriate permissions for target
    creation.
-   Never place passwords directly in source files or command history.
-   Confirm the target database name before destructive refresh
    operations.

------------------------------------------------------------------------

## 8. Database Strategy

### Development database: Employees

Use the MySQL Employees sample database for early development. It
includes employees, departments, department assignments, titles, and
salary-related relationships.

Use it to test schema inspection, dependency closure, safe insertion
order, baseline sampling, and simple workload queries.

### Evaluation database: Sakila

Use the Sakila sample database for a richer final demonstration
involving films, actors, customers, rentals, inventory, and payments.
Verify the actual schema and constraints before writing workload
queries.

### Synthetic datasets

Generate controlled datasets for scalability tests. Keep schema
structure and workload patterns documented while varying total row
counts. Synthetic data should have known distributions and valid
foreign-key relationships.

Do not assume Employees or Sakila is automatically installed with MySQL
Server; install and verify the sample database separately.

------------------------------------------------------------------------

## 9. Algorithm Design

### 9.1 Dependency closure

Let `S` be the current selected set. Define `closure(S)` as the set
containing `S` plus all required referenced parent rows, recursively,
under the supported foreign-key model.

A candidate is feasible only if its closure can be added without
exceeding the hard row budget.

The first version uses:

-   Single-column primary and foreign keys.
-   Acyclic foreign-key graph.
-   Explicit nullable-FK policy.
-   Clear rejection of unsupported composite keys or cycles.

### 9.2 Baseline A: Random sampling with repair

1.  Select seed rows randomly using a recorded seed.
2.  Calculate dependency closure.
3.  Reject infeasible candidates under the shared budget policy.
4.  Materialize and evaluate the subset.

Document the randomization unit, table allocation, candidate rejection
behavior, and handling of remaining budget. Avoid comparing a baseline
that can exceed the budget with a proposed method that cannot.

### 9.3 Baseline B: Referential sampling

1.  Use a documented table-aware or relationship-aware seed-selection
    policy.
2.  Calculate dependency closure.
3.  Enforce the same hard budget.
4.  Materialize and evaluate the subset.

This baseline should differ meaningfully from random sampling while
remaining straightforward and reproducible.

### 9.4 Proposed method: SafeSliceSQL workload-aware greedy

Let `S` be the current selection and `c` a candidate row or candidate
group. Use a heuristic such as:

\[ `\operatorname{score}`{=tex}(c `\mid `{=tex}S) =
`\frac{\Delta V(c \mid S)}`{=tex} {`\max`{=tex}(1,`\Delta `{=tex}D(c
`\mid `{=tex}S))} \]

Where:

-   `ΔV(c | S)` is the candidate's marginal workload-retention value.
-   `ΔD(c | S)` is the number of additional rows needed by its
    dependency closure, accounting for dependencies already selected.

Procedure:

1.  Initialize the selection according to the chosen policy.
2.  Generate feasible candidate rows or groups.
3.  Estimate marginal workload value for each candidate.
4.  Compute dependency cost and score.
5.  Choose the best feasible candidate according to the documented
    tie-breaking rule.
6.  Add the candidate and its required dependencies.
7.  Update query-relevance and marginal-value calculations.
8.  Repeat until no useful candidate fits the remaining budget.
9.  Materialize the final subset.
10. Evaluate actual query results; do not treat the heuristic's
    predicted value as a measured outcome.

The value function must be made concrete for the supported workload.
Possible initial features include whether a candidate participates in
relevant joins, satisfies supported filters, contributes to nonempty
query results, or represents a needed group. Avoid double-counting the
same query benefit without a defined rule.

The score is a heuristic, not an optimality guarantee. State its time
and memory complexity after implementation and measurement.

------------------------------------------------------------------------

## 10. SQL Workload and Metric Definitions

### Workload design

Begin with a small, fixed set of queries covering:

-   Selection and filtering.
-   Single-table and multi-table joins.
-   Foreign-key relationships.
-   Grouping and basic aggregates.
-   Queries that are intentionally empty in the source, if useful for
    testing metric edge cases.

Verify each query against the actual database. Record the source result
before subset generation.

### Metrics

  ---------------------------------------------------------------------------------
  Metric                              Definition and caveat
  ----------------------------------- ---------------------------------------------
  Query survival                      Fraction of queries nonempty on the source
                                      that remain nonempty in the subset

  Result-row retention                A defined comparison of source and subset
                                      result rows; use suitable key/tuple
                                      normalization where semantics require it

  Key retention                       Fraction of relevant source keys retained for
                                      queries with meaningful key-based results

  Aggregate retention                 Comparison of aggregate/group outputs under a
                                      declared matching rule and tolerance

  Referential violations              Count of broken supported foreign-key
                                      references; expected value is zero

  Row reduction                       `1 - subset_total_rows / source_total_rows`

  Budget deviation                    Actual rows minus the allowed maximum; must
                                      be nonpositive for a hard budget

  Runtime                             Separately record selection, materialization,
                                      validation, and workload-evaluation time

  Storage reduction                   Optional; measure with a consistent MySQL
                                      procedure and explain its limitations
  ---------------------------------------------------------------------------------

Do not treat an empty source query as automatically lost or preserved.
Exclude it from query-survival's denominator as specified, but report
empty-query cases separately if they matter to the workload.

For aggregate queries, define how groups and values are matched. For
queries without stable keys, use a documented multiset or
normalized-result comparison rather than assuming row order is
meaningful. Ignore result order unless the query specifies `ORDER BY`.

------------------------------------------------------------------------

## 11. Phase-wise Implementation Roadmap --- 18 Days

### Phase 1 --- Foundation and Scope (Days 1--2)

**Tasks** - Create repository, virtual environment, dependencies, and
configuration. - Install and verify Employees. - Define the supported
SQL and schema scope. - Create initial workload and module interfaces. -
Establish logging, naming conventions, and code-review rules.

**Files:** `pyproject.toml`, `README.md`, `.env.example`, `config.py`,
`workloads/workload.sql`, `docs/architecture.md`

**Exit criteria:** Every team member can connect to the same logical
source database and execute a workload query.

### Phase 2 --- Schema Analysis and Dependency Graph (Days 3--4)

**Tasks** - Inspect MySQL metadata. - Extract primary keys, foreign
keys, nullability, and row counts. - Build the dependency graph. -
Detect unsupported keys, cycles, and schema features. - Implement
dependency-closure unit tests.

**Files:** `schema_analyzer.py`, `dependency_graph.py`,
`test_schema.py`, `test_dependencies.py`

**Exit criteria:** Schema metadata is produced from the live database,
and closure finds required parent rows for supported cases.

### Phase 3 --- Subset Generation and Correctness (Days 5--6)

**Tasks** - Create isolated target schemas. - Reproduce supported schema
definitions. - Insert rows in safe dependency order. - Preserve
primary-key values. - Enforce hard budget. - Validate target integrity
and source immutability.

**Files:** `db.py`, `subset_generator.py`, `validator.py`, related tests

**Exit criteria:** A small target subset is generated, inspectable in
Workbench, within budget, and valid under supported constraints.

### Phase 4 --- Baseline Algorithms (Days 7--8)

**Tasks** - Implement random sampling with dependency repair. -
Implement referential sampling. - Standardize method interfaces. - Make
seeds and tie-breaking reproducible. - Use identical budget and
validation policies.

**Files:** `sampler.py`, tests, initial benchmark configuration

**Exit criteria:** Both baselines generate valid subsets and produce
comparable output records.

### Phase 5 --- Core SafeSliceSQL Algorithm (Days 9--11)

**Tasks** - Extract features from the supported workload. - Define
candidate units and marginal workload value. - Compute incremental
dependency cost. - Implement budget-aware greedy selection. -
Recalculate marginal values after each selection. - Log candidate
decisions and stop conditions. - Construct a controlled example where
workload-aware selection can demonstrate its intended benefit.

**Files:** `workload_analyzer.py`, `sampler.py`, `docs/algorithm.md`

**Exit criteria:** The proposed method completes end-to-end on the
supported example and respects the budget. Its benefit is measured
rather than assumed.

### Phase 6 --- Validation and Evaluation (Days 12--13)

**Tasks** - Implement PK/FK and budget validation. - Compare workload
results between source and target. - Implement per-query metrics. - Add
error handling for unsupported queries and schemas. - Verify
reproducibility and source immutability.

**Files:** `validator.py`, `benchmark.py`, `docs/metrics.md`, tests

**Exit criteria:** All methods produce structured correctness and
workload reports with clearly defined metrics.

### Phase 7 --- CLI Integration and Pilot Experiments (Days 14--15)

**Tasks** - Integrate modules into CLI commands. - Run an end-to-end
clean setup. - Add logs and artifact paths. - Run a small pilot on
Employees. - Fix performance and correctness bottlenecks before scaling.

**Files:** `cli.py`, `config.py`, `reporting.py`, README instructions

**Exit criteria:** Another member can run inspection, selection,
generation, validation, and evaluation without manual code edits.

### Phase 8 --- Benchmarks and Analysis (Day 16)

**Tasks** - Run fair comparisons across methods, sizes, budgets, and
seeds. - Save raw CSV files. - Generate plots from raw data. - Report
runtime, actual size, budget adherence, integrity, and per-query
metrics. - Add an ablation test if time allows.

**Files:** `benchmarks/benchmarks.yaml`, `results/raw/`,
`results/plots/`, `scripts/plot_results.py`

**Exit criteria:** Every chart and claim can be traced to recorded
experiment results.

### Phase 9 --- Finalization and Defense (Days 17--18)

**Tasks** - Run the full test suite. - Rehearse the demo on a clean
setup. - Complete report, architecture diagram, and algorithm
pseudocode. - Document limitations and future work. - Prepare viva
questions and backup demo artifacts.

**Files:** `README.md`, `docs/limitations.md`, `docs/demo_script.md`,
final report and presentation

**Exit criteria:** The complete workflow can be demonstrated
reproducibly and defended with evidence.

------------------------------------------------------------------------

## 12. Benchmark Plan

### Initial benchmark matrix

  Dimension            Values
  -------------------- ----------------------------------------------------
  Methods              Random with repair, referential sampling, SafeSliceSQL
  Total dataset rows   10K, 50K, 100K
  Row budgets          10%, 20%, 40%
  Seeds                3 per configuration

The full matrix contains up to `3 × 3 × 3 × 3 = 81` configurations
before additional ablations. Run a pilot first; reduce the matrix
transparently if runtime threatens the deadline.

### Fairness controls

-   Same source dataset and workload.
-   Same hard-budget definition.
-   Same supported schema and SQL features.
-   Same dependency-closure and materialization rules.
-   Same seeds where applicable.
-   Same measurement procedure.
-   Repeated trials where stochastic behavior matters.
-   Report actual output sizes, not only requested percentages.

### Expected output artifacts

-   Raw per-run CSV.
-   Per-query result comparison CSV.
-   Summary table grouped by method, size, and budget.
-   Workload-retention vs budget plot.
-   Runtime vs dataset-size plot.
-   Row-reduction and integrity summary.
-   Optional ablation plot.

Do not invent target results in advance. A sound report includes
negative or mixed findings.

------------------------------------------------------------------------

## 13. CLI Design

The intended CLI contract is:

``` bash
safeslice inspect --database employees_source

safeslice run --method random-repair --budget-percent 20 --seed 42

safeslice run --method referential --budget-percent 20 --seed 42

safeslice run --method workload-aware --budget-percent 20 --seed 42

safeslice validate --source employees_source --target employees_slice

safeslice benchmark --config benchmarks/benchmarks.yaml
```

Command options and names should be implemented consistently in `cli.py`
and documented in `--help`. Database names and credentials must come
from validated configuration, not be hardcoded.

Each run should log: - Run ID, timestamp, code version if available. -
Source/target names and schema fingerprint if implemented. - Method,
budget, seed, workload path, and supported-feature configuration. -
Selected seed rows and dependency additions, or a summary when full logs
are too large. - Actual row counts and validation results. - Per-query
metrics and runtime breakdown. - Output artifact paths and errors.

------------------------------------------------------------------------

## 14. Team Responsibilities and Integration

  -------------------------------------------------------------------------
  Member                  Main ownership          Deliverables
  ----------------------- ----------------------- -------------------------
  M1                      Database and dependency `db.py`,
                          engine                  `schema_analyzer.py`,
                                                  `dependency_graph.py`,
                                                  `subset_generator.py`

  M2                      Selection algorithms    `sampler.py`,
                          and workload analysis   `workload_analyzer.py`,
                                                  baselines, greedy
                                                  selector

  M3                      Validation,             `validator.py`,
                          integration, and        `benchmark.py`, `cli.py`,
                          reporting               tests, result plots and
                                                  report integration
  -------------------------------------------------------------------------

### Shared rules

1.  Agree on function signatures and result data structures before
    parallel implementation.
2.  Keep module contracts in `docs/architecture.md`.
3.  Use feature branches and pull requests.
4.  Merge small working components daily.
5.  Write unit tests alongside each module.
6.  Every member should understand architecture, algorithm, metrics, and
    limitations for the viva.

Suggested function contracts:

``` python
inspect_schema(engine) -> SchemaMetadata

select_rows(schema, workload, budget, method, seed) -> SelectionResult

materialize_subset(engine, selection, target_database) -> GenerationResult

validate_subset(source, target) -> ValidationReport

evaluate_workload(source, target, workload) -> WorkloadReport

run_benchmark(config) -> BenchmarkResults
```

These are design contracts to refine during Phase 1, not pre-existing
implementations.

------------------------------------------------------------------------

## 15. Testing Strategy

### Unit tests

-   Metadata extraction.
-   Parent-child edge direction.
-   Dependency closure.
-   Null foreign-key policy.
-   Budget calculation.
-   Candidate scoring and tie-breaking.
-   Query parsing for supported patterns.
-   Per-query metric calculation.

### Integration tests

-   Create a target schema.
-   Materialize a known selection.
-   Verify primary keys and foreign keys.
-   Run the workload on source and target.
-   Verify CLI exit codes and output artifacts.

### Edge cases

-   Missing parent row.
-   Empty table.
-   Query with empty source result.
-   Candidate whose closure exceeds the budget.
-   Unsupported composite key.
-   Foreign-key cycle.
-   Unsupported SQL query.
-   Database connection failure.
-   Duplicate run or existing target schema.
-   Source/target name collision.

### Reproducibility tests

-   Fixed seed and configuration should reproduce the same selection
    decisions.
-   Raw experiment records must include all parameters needed to
    interpret the run.
-   Plots must be generated from recorded data.

------------------------------------------------------------------------

## 16. Limitations and Risk Management

  -----------------------------------------------------------------------
  Risk                                Mitigation
  ----------------------------------- -----------------------------------
  Dependency closure consumes the     Reject infeasible candidates and
  budget                              report closure cost

  Complex schema features delay       Freeze the initial supported scope;
  implementation                      detect unsupported cases

  SQL parsing becomes too broad       Start with explicit query patterns
                                      and a fixed workload

  Workload metric rewards superficial Report multiple metrics and
  survival                            per-query results

  Greedy selector underperforms       Analyze candidate scoring,
                                      baselines, and ablations honestly

  Benchmark runtime exceeds schedule  Pilot early and reduce the matrix
                                      transparently

  Integration fails late              Daily merges, common interfaces,
                                      end-to-end test by Phase 3

  Source data is accidentally         Read-only source access where
  modified                            possible; target isolation

  Results cannot be reproduced        Preserve seeds, configs, raw CSV,
                                      code version, and instructions
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 17. Five-minute Demonstration Plan

  -----------------------------------------------------------------------
  Time                                Demonstration
  ----------------------------------- -----------------------------------
  0:00--0:40                          Explain the problem and show source
                                      schema

  0:40--1:20                          Run a baseline and show measured
                                      query-result loss

  1:20--2:20                          Run SafeSliceSQL using the same budget

  2:20--3:20                          Inspect the target database and
                                      show integrity validation

  3:20--4:20                          Show real benchmark plots and raw
                                      metric summaries

  4:20--5:00                          Explain the contribution,
                                      guarantees, and limitations
  -----------------------------------------------------------------------

Prepare a clean demo environment and backup screenshots or recorded
terminal output. Do not present fabricated metrics or an unverified
performance claim.

------------------------------------------------------------------------

## 18. Viva Questions to Prepare

1.  Why does foreign-key integrity not guarantee query-behavior
    preservation?
2.  What is dependency closure?
3.  Why can dependency closure exceed the remaining budget?
4.  Why use greedy selection rather than an optimal solver?
5.  How is marginal workload value calculated?
6.  How do joins, filters, and aggregates affect candidate scoring?
7.  How are empty source query results handled?
8.  How do you handle nullable FKs, composite keys, and cycles?
9.  How are baseline methods made fair?
10. What happens if the workload changes?
11. What guarantees are formal and which are empirical?
12. What evidence supports your conclusions?
13. What are the algorithm's time and memory costs?
14. What happens if the source database changes during a run?
15. What would you implement next with more time?

------------------------------------------------------------------------

## 19. Definition of Done

The project is complete when all of the following are true:

-   [ ] A fresh setup can be reproduced from the README.
-   [ ] Schema metadata is discovered automatically from MySQL.
-   [ ] The dependency graph and closure work for the documented scope.
-   [ ] A separate subset database can be generated.
-   [ ] The source database remains unchanged.
-   [ ] All methods respect the same hard row budget.
-   [ ] Referential integrity passes for supported constraints.
-   [ ] Workload evaluation records per-query results.
-   [ ] Baselines and SafeSliceSQL run under the same protocol.
-   [ ] Benchmark CSVs and plots are reproducible.
-   [ ] Unsupported cases and limitations are documented.
-   [ ] Report, demo, and viva preparation are complete.

## 20. Immediate Next Actions

1.  Create the GitHub repository and initial folder structure.
2.  Verify MySQL connectivity and install Employees if needed.
3.  Create a Python environment and install dependencies.
4.  Freeze the initial supported schema and SQL workload.
5.  Implement `inspect_schema(engine)` first.
6.  Build the dependency graph and test closure.
7.  Generate and validate the first small target subset before building
    the proposed algorithm.

**Guiding principle:** Build a correct, reproducible end-to-end system
first. Then make the selection algorithm innovative and demonstrate its
value with fair, measured experiments.
