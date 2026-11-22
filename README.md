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

## Sample downloads for later testing

Every input below has **more than 5,000 source rows**. For a relational database,
this means the sum across its base tables, not 5,000 rows in every table. Counts
exclude CSV headers and describe the standard upstream datasets. Confirm the
exact count in the app's input preview before slicing a downloaded version.

These recommendations target **SafeSliceSQL 0.1.0 with MySQL 8.x and InnoDB**.
Their schemas match the documented requirements; full MySQL testing remains to
be done. Composite primary keys and acyclic self-references are supported;
composite foreign keys and cross-table dependency cycles are not.

### MySQL sample databases

1. **World: 5,302 rows across 3 tables** (4,079 cities, 239 countries, 984 language
   records). A small relational test with a composite primary key. Download the
   [official World ZIP](https://downloads.mysql.com/docs/world-db.zip), extract
   `world-db/world.sql`, and follow the
   [MySQL installation guide](https://dev.mysql.com/doc/world-setup/en/world-setup-installation.html).
   Select `world` in SafeSliceSQL; start with **20%**, a maximum of **1,060 rows**.
   If the archive is unavailable, retry from the
   [official sample database page](https://dev.mysql.com/doc/index-other.html).
2. **Chinook: 15,607 rows across 11 tables** in the standard 1.4.5 dataset. Music
   sales, invoices, playlists, composite keys, and an employee hierarchy provide
   deeper dependency chains. Download **Chinook_MySql.sql** from the
   [publisher's releases](https://github.com/lerocha/chinook-database/releases),
   or use the [upstream MySQL script](https://raw.githubusercontent.com/lerocha/chinook-database/master/ChinookDatabase/DataSources/Chinook_MySql.sql).
   Load it and select `Chinook`; start with **10%**, a maximum of **1,560 rows**.
   Preserve the script's table-name capitalization in custom queries.
3. **Employees: 3,919,015 rows across 6 base tables** in the standard dataset;
   the largest table, `salaries`, has 2,844,047 rows. This exercises larger scans,
   historical records, composite primary keys, and multiple parent dependencies.
   Download the complete [datacharmer/test_db repository](https://github.com/datacharmer/test_db)
   using **Code > Download ZIP** and extract it. Start the MySQL client from the
   extracted directory and run `SOURCE employees.sql;` so companion dump paths
   resolve. Select `employees`; start with **1%**, a maximum of **39,190 rows**.
   Use the standard InnoDB dataset and query base tables; upstream views are not
   copied. Full-source inspection and validation still take time at a small
   budget. See [resource limits](docs/operations.md#limits-and-cancellation).

Load SQL scripts into a disposable MySQL instance before opening the database in
SafeSliceSQL; the file picker does not import `.sql` dumps. Upstream scripts can
drop or recreate their named databases/tables. For World or Chinook, use the
MySQL client's `SOURCE C:/path/to/extracted-file.sql;` with the actual path.
Use InnoDB as the default storage engine and keep source and output databases
separate. ClassicModels has fewer than 5,000 rows and is excluded from this list.

### Two CSV files above 5,000 rows

Save these raw links as `.csv` files, keeping their header rows:

- **[Diamonds CSV](https://raw.githubusercontent.com/mwaskom/seaborn-data/master/diamonds.csv)**:
  **53,940 rows, 10 columns**; mixed numeric-looking values and categories. Try
  **10%** for a maximum of **5,394 rows**.
- **[Taxis CSV](https://raw.githubusercontent.com/mwaskom/seaborn-data/master/taxis.csv)**:
  **6,433 rows, 14 columns**; timestamps, payment categories, and empty fields.
  Try **20%** for a maximum of **1,286 rows**.

These are [Seaborn example-data versions](https://github.com/mwaskom/seaborn-data).
Choose **CSV file > Choose file...** and import one file at a time. MySQL is still
required: imports preserve values as text, add `_safeslice_id`, and do not infer
foreign keys or numeric types. Use the relational samples for relationship tests.
Iris and Titanic are below the requested size and are excluded from this list.

### Custom queries and how to judge a sliced copy

Each workload below is also saved under `workloads/testing_*.sql`. Choose its file
under the app's custom SQL workload option before creating a copy. These files
contain only supported SELECT queries and use unqualified table names so the app
can execute the same query against source and target. Inspect execution errors
and exclusions in the report. The CLI accepts the file via `--workload`, for
example `--workload workloads/testing_chinook.sql`.

**Validity:** the row budget must pass, copied values must match their source
rows, primary keys must remain unique, and foreign-key violations must be zero.
Use **Verify my copy** and the built-in validator for all declared relationships.
In a configured terminal, the validation command is:

```powershell
.\.venv\Scripts\python.exe -m safeslicesql.cli validate --source world --target YOUR_ACTUAL_OUTPUT_DATABASE
```

Replace both names for the chosen source and the actual generated output database
shown in Results. The CLI uses the source/target server credentials in `.env`.

**Query retention:** selected detail rows and joined results should match source
values, but a slice may omit matching rows or whole groups. Counts, sums, and
averages can change; identical source/target aggregate totals are not required.
A zero orphan count also passes for an empty child table, so inspect table counts
and retained query results as well. These example workloads exercise behavior;
they do not prove that every business query retains its answer.

The manual checks below use LEFT JOINs and scalar subqueries, outside the app's
workload subset. **Run them separately in MySQL Workbench or the mysql client**,
first with the source schema selected and then with the output schema selected.
Do not put them in the workload files. Each `orphan_*` count must be **0** on both;
the sample checks supplement the full validator.

#### World queries

Use [testing_world.sql](workloads/testing_world.sql):

```sql
-- Large cities: compare retained city keys and values.
SELECT ID, Name, CountryCode, Population
FROM city
WHERE Population >= 1000000;

-- Official languages must retain their country parents.
SELECT cl.CountryCode, cl.Language, co.Name AS country_name
FROM countrylanguage cl
JOIN country co ON cl.CountryCode = co.Code
WHERE cl.IsOfficial = 'T';

-- Compare continent coverage; counts may decrease after slicing.
SELECT co.Continent, COUNT(*) AS city_count
FROM city ci
JOIN country co ON ci.CountryCode = co.Code
GROUP BY co.Continent;
```

Manual checks: `total_rows` should be **5,302 on the standard source** and no more
than the chosen budget on the target. Both orphan counts must be zero.

```sql
SELECT (SELECT COUNT(*) FROM city)
     + (SELECT COUNT(*) FROM country)
     + (SELECT COUNT(*) FROM countrylanguage) AS total_rows;

SELECT COUNT(*) AS orphan_cities
FROM city ci LEFT JOIN country co ON ci.CountryCode = co.Code
WHERE co.Code IS NULL;

SELECT COUNT(*) AS orphan_languages
FROM countrylanguage cl LEFT JOIN country co ON cl.CountryCode = co.Code
WHERE co.Code IS NULL;
```

#### Chinook queries

Use [testing_chinook.sql](workloads/testing_chinook.sql):

```sql
-- Invoice lines must still join to invoices, customers, and tracks.
SELECT il.InvoiceLineId, i.InvoiceId, c.CustomerId, t.TrackId, il.Quantity
FROM InvoiceLine il
JOIN Invoice i ON il.InvoiceId = i.InvoiceId
JOIN Customer c ON i.CustomerId = c.CustomerId
JOIN Track t ON il.TrackId = t.TrackId
WHERE c.Country = 'USA';

-- Exercise the two-column PlaylistTrack primary key and both parents.
SELECT pt.PlaylistId, pt.TrackId, p.Name AS playlist_name, t.Name AS track_name
FROM PlaylistTrack pt
JOIN Playlist p ON pt.PlaylistId = p.PlaylistId
JOIN Track t ON pt.TrackId = t.TrackId
WHERE p.PlaylistId = 1;

-- Compare retained genres; track counts may decrease.
SELECT g.GenreId, g.Name, COUNT(*) AS track_count
FROM Track t
JOIN Genre g ON t.GenreId = g.GenreId
GROUP BY g.GenreId, g.Name;
```

Manual checks: both counts must be zero. An invoice's stored `Total` need not
equal the sum of its retained lines: preserving a parent does not retain every
child, so that comparison is not a validity rule for a slice.

```sql
SELECT COUNT(*) AS orphan_invoice_lines
FROM InvoiceLine il
LEFT JOIN Invoice i ON il.InvoiceId = i.InvoiceId
LEFT JOIN Track t ON il.TrackId = t.TrackId
WHERE i.InvoiceId IS NULL OR t.TrackId IS NULL;

SELECT COUNT(*) AS orphan_playlist_tracks
FROM PlaylistTrack pt
LEFT JOIN Playlist p ON pt.PlaylistId = p.PlaylistId
LEFT JOIN Track t ON pt.TrackId = t.TrackId
WHERE p.PlaylistId IS NULL OR t.TrackId IS NULL;
```

#### Employees queries

Use [testing_employees.sql](workloads/testing_employees.sql). The first two queries
restrict employee keys to keep detailed result sets modest; the third measures
department coverage across current assignments.

```sql
SELECT s.emp_no, s.from_date, s.to_date, s.salary, e.first_name, e.last_name
FROM salaries s
JOIN employees e ON s.emp_no = e.emp_no
WHERE s.emp_no BETWEEN 10001 AND 11000 AND s.to_date = '9999-01-01';

SELECT de.emp_no, de.dept_no, d.dept_name, e.first_name, e.last_name
FROM dept_emp de
JOIN employees e ON de.emp_no = e.emp_no
JOIN departments d ON de.dept_no = d.dept_no
WHERE de.emp_no BETWEEN 10001 AND 11000 AND de.to_date = '9999-01-01';

SELECT d.dept_no, d.dept_name, COUNT(*) AS current_assignments
FROM dept_emp de
JOIN departments d ON de.dept_no = d.dept_no
WHERE de.to_date = '9999-01-01'
GROUP BY d.dept_no, d.dept_name;
```

Manual checks: both counts must be zero. Missing salary history or current
assignments for a retained employee can be expected; every retained child must
have its required parents.

```sql
SELECT COUNT(*) AS orphan_salaries
FROM salaries s LEFT JOIN employees e ON s.emp_no = e.emp_no
WHERE e.emp_no IS NULL;

SELECT COUNT(*) AS orphan_department_assignments
FROM dept_emp de
LEFT JOIN employees e ON de.emp_no = e.emp_no
LEFT JOIN departments d ON de.dept_no = d.dept_no
WHERE e.emp_no IS NULL OR d.dept_no IS NULL;
```

#### Queries for the imported CSV databases

The following table names assume importing each file alone with its original
filename. For a renamed file or folder import, substitute the actual name from
the import manifest or database preview. Include `_safeslice_id` when comparing
rows: CSVs can contain duplicate values. Use categorical filters rather than
numeric comparisons because imported values are stored as text.

Diamonds: [testing_diamonds.sql](workloads/testing_diamonds.sql).

```sql
SELECT _safeslice_id, carat, cut, color, price
FROM data_1_diamonds
WHERE cut = 'Ideal';

SELECT cut, COUNT(*) AS diamond_count
FROM data_1_diamonds
GROUP BY cut;
```

Taxis: [testing_taxis.sql](workloads/testing_taxis.sql).

```sql
SELECT _safeslice_id, pickup, dropoff, payment, total
FROM data_1_taxis
WHERE payment = 'credit card';

SELECT pickup_borough, COUNT(*) AS trip_count
FROM data_1_taxis
GROUP BY pickup_borough;
```

### When you are ready to test

Try Taxis, Diamonds, World, Chinook, then Employees. Select the matching workload,
create a copy, verify the budget and relationships, and export the report. Repeat
with the same seed and budget when comparing methods. The requested 5,000-row
minimum applies to source inputs; a smaller copy may contain fewer than 5,000 rows.

This checkout already contains Diamonds under `datasets/csv/` and Employees under
`datasets/mysql/`. Downloaded payloads are ignored by Git and are absent from a
fresh clone. See the [dataset guide](datasets/README.md) for local download records
and upstream terms. Full scope is in the
[technical reference](docs/technical.md#supported-scope).

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
