-- ============================================================
-- SafeSliceSQL — Initial SQL Workload (Employees Database)
-- ============================================================
-- This is the declared fixed workload for development and testing.
-- Each query has been designed to cover specific SQL patterns:
--   - Selection and filtering
--   - Single-table and multi-table joins
--   - Foreign-key relationship traversal
--   - Grouping and aggregation
--   - Edge cases (empty result)
--
-- IMPORTANT: Verify each query against the actual Employees
-- database before using as the official workload. Record the
-- source result row count alongside each query.
-- ============================================================

-- Q1: Simple selection with filter (single table, WHERE clause)
-- Pattern: SELECT + WHERE
SELECT emp_no, first_name, last_name, hire_date
FROM employees
WHERE hire_date > '1995-01-01';

-- Q2: Single-table aggregation with GROUP BY
-- Pattern: SELECT + GROUP BY + COUNT
SELECT gender, COUNT(*) AS cnt
FROM employees
GROUP BY gender;

-- Q3: Two-table join — current department assignments
-- Pattern: JOIN + WHERE (date filter)
SELECT e.emp_no, e.first_name, e.last_name, d.dept_name
FROM employees e
JOIN dept_emp de ON e.emp_no = de.emp_no
JOIN departments d ON de.dept_no = d.dept_no
WHERE de.to_date = '9999-01-01';

-- Q4: Multi-table join with filter — current Senior Engineers
-- Pattern: JOIN + WHERE (equality + date)
SELECT e.emp_no, e.first_name, t.title
FROM employees e
JOIN titles t ON e.emp_no = t.emp_no
WHERE t.to_date = '9999-01-01' AND t.title = 'Senior Engineer';

-- Q5: Aggregate with grouping over join — employees per department
-- Pattern: JOIN + GROUP BY + COUNT
SELECT d.dept_name, COUNT(*) AS emp_count
FROM departments d
JOIN dept_emp de ON d.dept_no = de.dept_no
WHERE de.to_date = '9999-01-01'
GROUP BY d.dept_name;

-- Q6: Aggregate with HAVING — high earners
-- Pattern: JOIN + GROUP BY + MAX + HAVING
SELECT e.emp_no, e.first_name, e.last_name, MAX(s.salary) AS max_salary
FROM employees e
JOIN salaries s ON e.emp_no = s.emp_no
GROUP BY e.emp_no, e.first_name, e.last_name
HAVING MAX(s.salary) > 100000;

-- Q7: FK chain traversal — current department managers
-- Pattern: Multi-JOIN through FK chain
SELECT d.dept_name, e.first_name, e.last_name
FROM departments d
JOIN dept_manager dm ON d.dept_no = dm.dept_no
JOIN employees e ON dm.emp_no = e.emp_no
WHERE dm.to_date = '9999-01-01';

-- Q8: Intentionally empty result (edge case for metrics)
-- Pattern: WHERE with impossible condition
SELECT * FROM employees WHERE emp_no = -1;
