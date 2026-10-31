-- Run this in MySQL to create separate, empty target databases for 1% runs.
-- This does not create subsets itself; SafeSliceSQL materializes them when run.
-- Safe to re-run. Existing target databases are not dropped by this script.
CREATE DATABASE IF NOT EXISTS employees_random_1pct;
CREATE DATABASE IF NOT EXISTS employees_referential_1pct;
CREATE DATABASE IF NOT EXISTS employees_workload_1pct;