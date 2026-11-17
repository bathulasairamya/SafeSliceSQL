# SafeSliceSQL

Create a smaller MySQL database under a hard row budget while preserving supported
foreign-key relationships. Compare random-repair, referential, and workload-aware
sampling, then inspect integrity checks and query-retention results.

## Start here

This repository has four maintained guides:

- **This README:** installation, first run, and demo checklist.
- [Technical reference](docs/technical.md): architecture, algorithms, metrics, and limitations.
- [Operations guide](docs/operations.md): CLI runs, benchmarks, credentials, limits, and recovery.
- [Dataset guide](datasets/README.md): downloaded CSVs and MySQL sample databases.

## Install and configure

Requires **Python 3.10+** and **MySQL 8.x with InnoDB**. CSV/Excel imports also need
MySQL; SQLite is used for local generated tests. Run these PowerShell commands
from the project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Set the `SAFESLICE_SOURCE_*` and `SAFESLICE_TARGET_*` connection values in `.env`.
Keep source and target databases distinct. Existing sources need read access;
the desktop's imported sources and fresh targets need CREATE/DROP DATABASE and
table write privileges on the target server. See the operations guide for grants
and credential storage. Never commit `.env` or put real passwords in commands.

`requirements.txt` installs the package plus test, Excel, and credential-vault
support. For the pinned Windows/Python 3.10 demo environment, use
`requirements-demo-windows-py310.txt` instead in a Python 3.10 virtual environment.
Other platforms/Python versions should use `requirements.txt` and run the checks below.

## Create and verify a copy

```powershell
.\demo.bat
```

1. Choose an existing MySQL database, a UTF-8 CSV file/folder, or an `.xlsx` workbook.
   Start with `examples/demo-orders.csv` or a file listed in the dataset guide.
2. Review input statistics, set the percentage, and click **Create smaller copy**.
   The budget includes required parent records across all tables.
3. Open **Results > Verify my copy** to compare copied values and follow declared
   relationships. **View charts** shows size and integrity results.
4. Under **Options**, enable comparison of all three methods or provide a business
   SQL workload and acceptance thresholds. The desktop defaults to 100% query survival.
5. Use **Export report** for a self-contained HTML report; browser printing can save
   a PDF. **Previous jobs** can reopen saved results or restore choices for a new run.

CSV/Excel files use headers in the first row. Values are imported as text with
added row IDs; numeric/date types and foreign keys are not inferred. Excel formulas
remain text. Use a relational MySQL source to demonstrate relationship preservation.
Imported sources and generated copies remain on MySQL until explicitly cleaned up.

For the numbered terminal menu:

```powershell
& '.\Terminal Demo.bat'
```

Choose **9** to prepare a dataset, **3** to preview selection, **4** to create and
validate a copy, or **5** to compare methods. CLI runs and benchmark YAML are covered
in the operations guide.

## Check before presenting

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not db" -q --tb=short
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m safeslicesql.cli selftest
```

For a MySQL check, configure target-server credentials with permission to create
and remove disposable test schemas, then run:

```powershell
.\.venv\Scripts\python.exe -m safeslicesql.cli scalecheck --mysql --rows 1000 --seed 42 --cases 5
```

Rehearse the chosen dataset: create a copy, verify it, compare methods, and open
its report. The fictional demo CSV at 25% should produce 5 of 20 rows. Keep a saved
report available and identify it as saved evidence if the live server is unavailable.

## Scope and project files

Supported schemas require a primary key on every table, single-column foreign
keys, and acyclic cross-table dependencies. Composite primary keys and acyclic
self-references are supported. Workloads support a restricted read-only SELECT
subset. Unsupported queries are reported; inspect exclusions and require critical
queries explicitly. Integrity does not imply unchanged query answers, and no
sampling method is guaranteed to win. Full scope and metric rules are in the
technical reference.

- `src/safeslicesql/`: application code; `tests/`: automated checks.
- `workloads/` and `benchmarks/`: SQL workloads and experiment configuration.
- `datasets/`: local samples; `examples/`: tiny bundled demo input.
- `results/`: generated reports, metrics, and job records; `logs/`: diagnostics.

Historical plans and the old 1% report are available in Git history. Their algorithm
and metric definitions predate the current implementation; regenerate results
before using them in a presentation.

## License

Repository-wide license terms have not been specified. Third-party datasets retain
their upstream terms; consult their sources before redistribution.
