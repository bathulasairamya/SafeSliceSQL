# SafeSliceSQL

Create a smaller MySQL database under a hard row budget while preserving supported
foreign-key relationships. Compare random-repair, referential, and workload-aware
sampling, then inspect integrity checks and query-retention results.

## Windows quick start

The first demo uses the bundled [demo-orders.csv](examples/demo-orders.csv).
You'll make a **5-row copy of a 20-row dataset**. The app creates both MySQL
databases for this demo, so you can start with an empty server.

### 1. Get the repo and install the app

You'll need Git, Python 3.10 or newer with Tkinter, and a running **MySQL 8.x**
server with InnoDB. MySQL is required even for a CSV input because the smaller
copy is stored in MySQL. If you need a local server, follow the
[MySQL 8.4 Windows setup guide](https://dev.mysql.com/doc/refman/8.4/en/windows-installation.html).
Workbench alone does not install or start the server.

Open PowerShell in the folder where you want the project:

```powershell
git clone https://github.com/bathulasairamya/SafeSliceSQL.git
cd SafeSliceSQL
```

Already cloned it? Open PowerShell in that checkout and run `git pull --ff-only`.
Run all remaining commands from the folder containing `demo.bat`.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-demo-windows-py310.txt "PyMySQL[rsa]"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

The example uses the pinned Windows/Python 3.10 dependencies. If you have a newer
Python, use `py -3 -m venv .venv` and replace the requirements filename with
`requirements.txt`. The [PyMySQL RSA extra](https://github.com/PyMySQL/PyMySQL#installation)
supports MySQL accounts using SHA-2 password authentication.

### 2. Point the app at your MySQL server

In `.env`, set `SAFESLICE_TARGET_HOST`, `SAFESLICE_TARGET_PORT`,
`SAFESLICE_TARGET_USER`, and `SAFESLICE_TARGET_PASSWORD` to your own server and
credentials. For a local install, the example host and port are `localhost` and
`3306`. Save the file as `.env` and keep it private.

For the CSV demo you can leave the source settings and both database names from
`.env.example` as they are. The app imports the CSV onto the **target server**
and creates fresh `safeslice_input_…` and `safeslice_output_…` databases. Your
target account needs permission to create, write to, and clean up those schemas.
For a shared server, ask the administrator for the
[demo account grants](scripts/least_privilege_mysql.sql).

### 3. Run and show the demo

```powershell
.\demo.bat
```

1. Select **CSV file**, click **Choose file...**, and open `examples/demo-orders.csv`.
   The input preview should show **20 rows**.
2. Click **25%**, leave the other settings at their defaults, and click **Create
   smaller copy**.
3. On **Results**, check that **Rows in the smaller copy** shows **5 / 5 allowed**.
4. Open **Verify my copy** to compare original and copied values. Open **View
   charts** to show the size reduction or **Export report** to save an HTML report.

If the desktop window doesn't open, run it in PowerShell to see the error:

```powershell
.\.venv\Scripts\python.exe -m safeslicesql.desktop
```

If the job cannot connect, check that MySQL is running and that the host and
port match `.env`. For **Access denied**, check the credentials and privileges
in **Options → MySQL connections...**. If the server is unavailable, you can
still run the SQLite self-test below to exercise the pipeline locally.

## Try a relational demo

To show foreign-key preservation, choose an existing MySQL schema such as
the [World sample database](datasets/README.md). Configure the
`SAFESLICE_SOURCE_*` values for a read-only account on that database, then choose
**MySQL database** in the app. Required parent rows count toward the copy budget.
After creating the copy, use **Verify my copy** to follow a child row to its parent.
File imports store values as text with generated row IDs and do not infer relationships.

Under **Options**, you can compare the three selection methods or supply your own
SQL workload. Comparison mode exports additional charts and leaves the last
method's copy in the target. Imported sources and generated copies stay on MySQL
after the demo. See the [operations guide](docs/operations.md#explicit-retention)
for cleanup.

For the numbered terminal menu:

```powershell
& '.\Terminal Demo.bat'
```

Choose **9** to prepare the bundled CSV, **3** to preview selection, **4** to create
and validate a copy, or **5** to compare methods. See the
[operations guide](docs/operations.md) for the CLI and benchmarks, the
[technical reference](docs/technical.md) for algorithms and metrics, and the
[dataset guide](datasets/README.md) for other demo inputs.

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
