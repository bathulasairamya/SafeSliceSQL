-- Employees: bounded salary results, current assignments, and group retention.
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
