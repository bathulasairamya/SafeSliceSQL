# Production controls and pilot operation

SafeSliceSQL now provides bounded jobs, cancellation, durable local job records,
credential references, owned-schema retention, measured synthetic scale checks,
and application-query acceptance requirements. These are foundations for a
controlled, single-user pilot within the supported MySQL schema/SQL scope.
They do not establish production certification or support for arbitrary schemas.

## Use the desktop

1. Run `demo.bat` and choose the source database or input file.
2. Open **Options**. Choose your application's SQL file if you have one.
   Automatic queries are structural probes, not evidence of business suitability.
3. Set minimum query survival and exact result retention percentages. Optionally
   enter required query IDs such as `Q1,Q5`. IDs follow statement order in the SQL
   file. The desktop defaults to 100% survival and 0% exact result retention.
4. Create the copy. Both integrity checks and query quality requirements must pass
   before the desktop labels the copy ready. Failure details and CSV reports
   explain a rejected copy; increase the budget or review the workload/method.
5. Press **Cancel** while work is running to request a safe stop. It may take until
   the current database statement returns or times out. Keep the window open.
6. **Previous jobs** shows completed, rejected, failed, stopped, and interrupted jobs.
   Restore a desktop job's choices and click Create to restart from the current
   original input into a fresh target. This is a full restart, not partial resume.

A query surviving means it still produces an eligible result under the project's
metric definitions. It does not mean every answer is unchanged. Exact result
retention compares typed output rows; aggregate values can change when input rows
are removed. Review the per-query and aggregate metrics as well as survival. See
[metrics](metrics.md) for denominator and empty-result rules. Required query IDs
that are missing, excluded, failed, or lost reject the copy.

For saved benchmark YAML, add an acceptance section (fractions from 0 to 1):

```yaml
acceptance:
  min_query_survival: 1.0
  min_result_retention: 0.2
  required_queries: [Q1, Q5]
```

The CLI `run` command has matching `--min-query-survival`,
`--min-result-retention`, and repeatable `--required-query` options. A rejected
run exits with code 1 and exports acceptance errors in its CSV. Existing CLI
commands/YAML without a policy keep zero minimums for compatibility; choose a
policy explicitly when qualifying an application workload. Cancelled operations
exit 130 and cooperative resource-limit stops exit 124.

Quality gates report whether a selection is acceptable. They do not force the
heuristic selector to find a better selection or guarantee that workload-aware
selection outperforms random-repair. An excluded query only rejects acceptance
if it is explicitly required; inspect exclusions for your application's SQL.

## Limits and cancellation

Copy jobs, desktop discovery, and the CLI's inspect/run/validate/benchmark/selftest/
scalecheck commands have an execution context. `.env.example` lists the defaults:

- Job time: 600 seconds; MySQL connection establishment: 10 seconds.
- Statement setting: 30 seconds. MySQL uses `MAX_EXECUTION_TIME` for eligible
  SELECTs, metadata/InnoDB lock wait limits, and socket read/write timeouts of
  statement time plus five seconds. SQLite uses a VM progress callback.
- Source size: 5,000,000 rows; each query result stream: 1,000,000 rows.
- Input files: 100 MiB total; an XLSX archive's expanded size is also checked.
- Imported cell: 1 MiB; table count: 100; columns per table: 500.
- Exact-comparison SQLite database: 512 MiB; client process RSS: 1 GiB.

Override a limit with a positive integer such as
`SAFESLICE_LIMIT_JOB_SECONDS=1800` in `.env`. Byte limits use bytes. These values
are policy defaults, not claims that the application has been tested at every
configured maximum. Restart the desktop after changing environment settings.

Deadline, cancellation, and RSS checks run at checkpoints; RSS sampling is at most
once per half second. MySQL statement settings have server-defined scope and
socket timeouts bound individual network waits. These are cooperative controls,
not an OS-enforced total wall-clock, memory, or disk quota. Driver buffering,
native libraries, temporary sort files, and a single large fetched row can exceed
the requested process/file budget before the next check. The comparison page cap
is per temporary comparison database, not a quota on all job artifacts. Use a
supervised process/container and server-side resource policy for hard isolation.
Review application SQL as trusted input; these controls are not a hostile SQL sandbox.

Cancellation before the target DML commit rolls back that transaction. MySQL DDL
is not transactional. Newly created empty tables/schema remnants can remain after
an interruption. Cancellation or a quality failure after a commit does not restore
the previous target. Such a target requires review and is not accepted by the app.
Desktop jobs use fresh targets; CLI refreshes an explicitly selected test target.

Copies and their evaluation acquire a per-target lease: a MySQL named lock on
the connected server or an OS file lock for file-backed SQLite. Another SafeSlice
worker fails after five seconds instead of concurrently refreshing that target.
External writers that do not participate in the lock can still change a source
or target. Source fingerprints compare before/after contents, not all intervening
writes. Schedule against a stable source or a managed snapshot.

Library callers should create engines using `runtime.bounded_engine` inside
`runtime.execution_context`, with `read_only=True` for a pre-existing source.
Supplying arbitrary pre-created engines does not install these connection hooks.

## Job records and recovery

Desktop records live beside their reports in `results/desktop/<id>/job.json`.
CLI records live in `results/jobs/<id>/job.json`. Writes use an atomic replacement;
records contain the local OS owner/host, PID and process creation time, phase,
limits, state, and exact newly created schema names and MySQL server UUIDs.
Passwords are excluded. Desktop records store input paths and choices; CLI records
store the command name. CLI arguments must be supplied again for a restart.

From an activated environment:

```cmd
safeslice jobs list
safeslice jobs recover
safeslice jobs cancel --folder results/jobs/REPLACE_WITH_JOB_ID
```

Recovery checks the owning process identity, then marks an abandoned running job
interrupted. It never promotes a partial copy to a completed one or resumes
in-flight SQL. Current-owner/current-host checks prevent accidental cross-user
management. They are not authentication or tamper protection against someone who
can edit the local job files. Use a separate OS account and private workspace per
operator. POSIX job directories/files are restricted to their owner; Windows
inherits filesystem ACLs, which the administrator must restrict appropriately.

## Credentials and least privilege

Use separate MySQL accounts for reading the existing source and writing app-owned
schemas. Source connections created by the app also use a read-only session.
The administrator-reviewed example in
[`scripts/least_privilege_mysql.sql`](../scripts/least_privilege_mysql.sql) provisions
a reader on one source and a writer on the generated input/output name prefixes.
It is never executed automatically. Replace `SOURCE` with the real source name
and configure the resulting users in `.env`. CLI targets outside the generated
prefixes need their own explicit administrator-reviewed grants.

Prefix grants depend on `partial_revokes=OFF`; literal underscores/percent signs
must be escaped in database-level grants. If partial revokes are enabled, have
the administrator provision exact schema grants. This behavior follows the
[MySQL GRANT documentation](https://dev.mysql.com/doc/refman/8.4/en/grant.html).
The example uses generated passwords, requiring MySQL 8.0.18+; see
[MySQL password management](https://dev.mysql.com/doc/refman/8.0/en/password-management.html).
Generated-database testing needs separate disposable-schema privileges and is
best run on a dedicated test server. Do not grant the writer access to your
existing source or use `root` for a deployed pilot.

Plain environment passwords remain supported for local setup. To store passwords
in the OS user's native credential vault instead:

```cmd
python -m pip install -e ".[security]"
safeslice store-password source
safeslice store-password target
```

Configure each role's host, port, and username first. The commands prompt without
echo and print the non-secret service reference. Set these in `.env` and remove
the corresponding plaintext password:

```dotenv
SAFESLICE_SOURCE_PASSWORD_KEYRING_SERVICE=SafeSliceSQL/source
SAFESLICE_TARGET_PASSWORD_KEYRING_SERVICE=SafeSliceSQL/target
```

Vault entries are keyed by `user@host:port`; update them when those settings change.
Only supported native OS backends are accepted; plaintext/chained keyring backends
fail closed. Alternatively use `SAFESLICE_SOURCE_PASSWORD_FILE` and/or
`SAFESLICE_TARGET_PASSWORD_FILE` referencing a private UTF-8 secret file. On POSIX
its group/other permission bits must be zero. On Windows protect it using ACLs.
Do not select both file and vault for the same role. A configured reference that
cannot be read fails instead of falling back to a plaintext environment password.

The app sanitizes database exception text to omit SQL and bound row values, redacts
registered passwords, and rotates diagnostic logs. Reports still contain schema
names, input paths, query metrics, and possible workload literals. Imported sources
and copies contain the original data: there is no automatic PII masking, encryption
at rest, remote authentication service, or multi-user authorization layer. Deploy
on private storage and a trusted database network appropriate to your data policy.

## Explicit retention

Successful imported sources and generated copies remain available for inspection.
Retention cleanup is an explicit operation, never a desktop startup side effect:

```cmd
safeslice jobs cleanup --days 7
```

This removes only expired schemas recorded as created by a terminal job belonging
to the current local user/host, with an exact generated-name pattern and matching
MySQL server UUID. It does not drop an existing user-selected source or arbitrary
named CLI target, and it keeps report files. The age is measured from the job's
last record update. Supply the same target-server credentials used for that job.
If ownership/server checks fail, cleanup stops rather than guessing.

A hard crash between `CREATE DATABASE` and the post-create ownership record can
leave a `pending_schemas` entry. Automatic cleanup does not delete such unproven
schemas. An administrator must verify them manually. Reports/logs, input files,
and hard-crash temporary files follow your separate filesystem retention policy.

## Repeatable scale evidence

```cmd
safeslice scalecheck --rows 1000 --rows 5000 --seed 42 --cases 5
safeslice scalecheck --mysql --rows 1000 --rows 5000 --seed 42 --cases 5
```

The first uses local SQLite. The second creates and deletes uniquely named
disposable schemas on the configured target MySQL server. Both exercise three
selectors at 10% and 100% budgets, repeat selections to check reproducibility,
verify FK/budget/schema/source integrity, and check exact full-budget results.
Five cases cover chain, star, branching, self-reference, and independent tables,
with varied names, counts, nulls, skew, and composite primary keys.

`results/scale/<id>/evidence.json` records the backend/runtime/platform, requested
row levels, actual total source rows per case, elapsed and client CPU time,
sampled peak client RSS, retained artifact bytes, and check/failure counts.
Each completed scale sample is saved; interrupted current samples may be partial.
New evidence files record `running`, `completed`, `failed`, or `stopped` and identify
an incomplete sample explicitly. A stopped run is not a completed capacity test.
MySQL server memory, disk size, CPU, network throughput, and concurrent-user load
are not measured. Evidence is explicitly marked synthetic and not production
certification. Increase levels deliberately under a test-server resource policy,
then benchmark representative customer schemas and application SQL before setting
capacity targets or claiming a supported production size.

CI retains the Python unit-test matrix and adds a disposable MySQL 8 service for
integration tests plus a repeatable 1,000/5,000-row scale sweep. Its scale reports
are uploaded as artifacts. A configured workflow is not proof of a successful
hosted run; review its actual status after pushing your changes.
