# SafeSliceSQL live demonstration

Before the presentation, install `requirements.txt`, configure MySQL credentials,
and check that the target account can create databases. Use supported schema and
SQL features as described in [limitations](limitations.md).

For the tested Windows/Python 3.10 presentation environment, install
`requirements-demo-windows-py310.txt` instead. It pins the application and test
dependencies, including Excel and OS credential support. Use a fresh environment
for a different Python/platform combination and run the checks below again.

Before presenting, run from the repository root:

```cmd
.venv\Scripts\python.exe -m safeslicesql.cli --help
.venv\Scripts\python.exe -m pytest -m "not db" -q --tb=short
.venv\Scripts\python.exe -m safeslicesql.cli scalecheck --mysql --rows 1000 --seed 42 --cases 5
```

The scale check creates and cleans up only its own generated schemas. Inspect its
saved evidence and resolve any failed checks before the presentation. Start with
a small, supported dataset and a generous budget; reduce it after showing a
successful copy. For business queries, set acceptance requirements explicitly.
Rejected copies are rolled back and must not be described as accepted results.

1. Double-click `demo.bat` to open the desktop interface.
   For a quick rehearsal, select `examples/demo-orders.csv` and keep 25%: the
   expected copy has 5 of 20 rows. Click **Verify my copy** to compare every
   copied record with its original. The bundled data is fictional demo data.
2. Ask faculty to choose an existing MySQL database, CSV file/folder, or Excel
   workbook. Use the database list or native file picker.
3. Set the percentage slider and click **Create smaller copy**. Explain that the
   limit counts rows across all tables and includes required parent records.
4. Show original/copy row counts and the integrity checks. The source stays intact;
   a fresh MySQL target is created. Open the reports folder for details.
5. Change the percentage and run again to answer a live question.
6. Under **Advanced options**, enable comparison of all three methods. Show the
   charts and measured query retention; report the observed winner without
   assuming the workload-aware method is always best.

File imports use text columns with added row IDs. Relations between separate files
are not inferred. Generated queries demonstrate structural coverage, not the
business meaning of a dataset. Use a declared business workload through the
terminal interface when that is the experiment being evaluated.

For terminal controls, double-click `Terminal Demo.bat`. Option 9 prepares faculty
input, 3 previews selection, 4 creates/validates a copy, 5 compares methods, and
8 runs generated-database checks. Results are stored in separate `results/` folders.

Keep a prior report available if the presentation machine's database connection
is unavailable. Clearly distinguish saved evidence from a live run.
