# SafeSliceSQL live demonstration

Before the presentation, install `requirements.txt`, configure MySQL credentials,
and check that the target account can create databases. Use supported schema and
SQL features as described in [limitations](limitations.md).

1. Double-click `demo.bat` to open the desktop interface.
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
