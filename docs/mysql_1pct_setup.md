# Local MySQL setup for 1% SafeSliceSQL runs

## 1. Configure credentials

This optional example requires a populated MySQL Employees sample database. From the repository root, copy `.env.example` to `.env` if needed and configure your own connection credentials. Set the source database to `employees`; use a separate target. For arbitrary faculty input, use the desktop interface instead.

Use an account that can read the source and create tables and delete/insert rows in the target. Avoid committing `.env` (it is ignored by Git).

## 2. Create the empty target databases

From a MySQL client, execute [`scripts/create_1pct_targets.sql`](../scripts/create_1pct_targets.sql), or paste:

```sql
CREATE DATABASE IF NOT EXISTS employees_random_1pct;
CREATE DATABASE IF NOT EXISTS employees_referential_1pct;
CREATE DATABASE IF NOT EXISTS employees_workload_1pct;
```

These are empty targets, not copies. SafeSliceSQL creates/populates subset tables during each run. The script does not drop existing databases.

## 3. Install and verify

From the repository root, install the requirements if needed and test the source connection/schema:

```powershell
python -m pip install -r requirements.txt
python -m safeslicesql.cli inspect --database employees
```

Confirm the source connection succeeds and the database schema is supported before creating any subset.

## 4. Run 1% subset generation

Use a dedicated target per method. Each run may transactionally refresh rows inside the specified target database, so do not point it at a database containing data you need to keep.

```powershell
python -m safeslicesql.cli run --method random-repair --budget-percent 1 --seed 42 --source employees --target employees_random_1pct --output-dir results/1pct/random-repair
python -m safeslicesql.cli run --method referential --budget-percent 1 --seed 42 --source employees --target employees_referential_1pct --output-dir results/1pct/referential
python -m safeslicesql.cli run --method workload-aware --budget-percent 1 --seed 42 --source employees --target employees_workload_1pct --output-dir results/1pct/workload-aware
```

`--budget-percent 1` means a maximum of approximately 1% of the source's total rows, rounded down with a minimum budget of one row. Dependency closure is included in the budget; the selected subset may be smaller than the budget if no further candidate closure fits. Check the command's validation summary and CSV output for the actual selected row count and integrity result.


The source dataset must already be imported, and MySQL must be running. This
guide does not assume a particular repository path or an existing local `.env`.
