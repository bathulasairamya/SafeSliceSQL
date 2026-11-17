# Test datasets

[Quick start](../README.md) | [Operations](../docs/operations.md)

Downloaded datasets live here, separately from application code and generated results.
The CSVs and SQL archives have been checked for structural integrity. They have
not yet been imported and tested end to end in SafeSliceSQL.

## CSV files

Choose one file under `csv/` in SafeSliceSQL's file-import flow:

- `iris.csv`: 150 rows; a small first test.
- `tips.csv`: 244 rows; restaurant bills and categories.
- `penguins.csv`: 344 rows; categorical data and missing values.
- `titanic.csv`: 891 rows; mixed columns and missing values.
- `diamonds.csv`: 53,940 rows; a larger sampling and performance test.

Source: [Seaborn example-data repository](https://github.com/mwaskom/seaborn-data).
The files are independent examples, not related tables. SafeSliceSQL imports CSV
values as text and adds a synthetic primary key; it does not infer foreign keys.
Selecting the entire `csv/` folder imports these independent tables together.

## MySQL databases

- `mysql/classicmodels/mysqlsampledatabase.sql`: eight related business tables.
  Source: [ClassicModels download and schema](https://www.mysqltutorial.org/getting-started-with-mysql/mysql-sample-database/).
- `mysql/employees/employees.sql`: the entry point for the larger Employees database.
  Its companion dump files, upstream README and verification scripts are in the
  same directory. Source: [Employees repository](https://github.com/datacharmer/test_db).

Load these using a MySQL client before connecting SafeSliceSQL to the resulting
database. Run the Employees import from its own directory so its relative SOURCE
paths resolve. Follow the upstream README for its verification commands.
Use a disposable MySQL instance/schema: upstream SQL scripts can drop and recreate
their named databases or tables. Downloading these files has not executed any SQL.

Start with ClassicModels for relational testing; use Employees for larger runs
after checking runtime and row limits. These downloads are not a compatibility
certification for every schema feature or query.

Original ZIP downloads are kept in `archives/`. Upstream notices inside the
downloads are retained; consult each source's terms before redistributing data.
Downloaded payloads are excluded from Git by this folder's `.gitignore`.

## Download record

`manifest.json` records download time, source URLs, file sizes, CSV row counts and
SHA-256 checksums. ZIP CRC checks passed before extraction. Checksums identify
these local downloads; they are not publisher-provided authenticity signatures.

The World database could not be downloaded: Oracle returned a technical-difficulties
page for both official ZIP and TGZ archives. Retry from the
[official MySQL sample database page](https://dev.mysql.com/doc/index-other.html).
