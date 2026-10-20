"""
scripts/seed_sqlite_db.py — Generate a realistic SQLite database matching the MySQL Employees schema.

Creates:
- data/employees_source.db with populated relational data (departments, employees, dept_emp, dept_manager, titles, salaries)
- data/employees_target.db as an empty initial target database
"""

from __future__ import annotations

import datetime
from pathlib import Path
import random
import sqlite3
import sqlalchemy as sa
from sqlalchemy import text


def seed_database(db_path: Path | str = "data/employees_source.db") -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    engine = sa.create_engine(f"sqlite:///{path.as_posix()}")

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON;"))

        # 1. departments
        conn.execute(text("""
            CREATE TABLE departments (
                dept_no VARCHAR(4) PRIMARY KEY,
                dept_name VARCHAR(40) NOT NULL UNIQUE
            );
        """))

        # 2. employees
        conn.execute(text("""
            CREATE TABLE employees (
                emp_no INTEGER PRIMARY KEY,
                birth_date DATE NOT NULL,
                first_name VARCHAR(14) NOT NULL,
                last_name VARCHAR(16) NOT NULL,
                gender VARCHAR(1) NOT NULL,
                hire_date DATE NOT NULL
            );
        """))

        # 3. dept_emp (Composite PK + 2 FKs)
        conn.execute(text("""
            CREATE TABLE dept_emp (
                emp_no INTEGER NOT NULL,
                dept_no VARCHAR(4) NOT NULL,
                from_date DATE NOT NULL,
                to_date DATE NOT NULL,
                PRIMARY KEY (emp_no, dept_no),
                FOREIGN KEY (emp_no) REFERENCES employees (emp_no) ON DELETE CASCADE,
                FOREIGN KEY (dept_no) REFERENCES departments (dept_no) ON DELETE CASCADE
            );
        """))

        # 4. dept_manager (Composite PK + 2 FKs)
        conn.execute(text("""
            CREATE TABLE dept_manager (
                emp_no INTEGER NOT NULL,
                dept_no VARCHAR(4) NOT NULL,
                from_date DATE NOT NULL,
                to_date DATE NOT NULL,
                PRIMARY KEY (emp_no, dept_no),
                FOREIGN KEY (emp_no) REFERENCES employees (emp_no) ON DELETE CASCADE,
                FOREIGN KEY (dept_no) REFERENCES departments (dept_no) ON DELETE CASCADE
            );
        """))

        # 5. titles (Composite PK + FK)
        conn.execute(text("""
            CREATE TABLE titles (
                emp_no INTEGER NOT NULL,
                title VARCHAR(50) NOT NULL,
                from_date DATE NOT NULL,
                to_date DATE NOT NULL,
                PRIMARY KEY (emp_no, title, from_date),
                FOREIGN KEY (emp_no) REFERENCES employees (emp_no) ON DELETE CASCADE
            );
        """))

        # 6. salaries (Composite PK + FK)
        conn.execute(text("""
            CREATE TABLE salaries (
                emp_no INTEGER NOT NULL,
                salary INTEGER NOT NULL,
                from_date DATE NOT NULL,
                to_date DATE NOT NULL,
                PRIMARY KEY (emp_no, from_date),
                FOREIGN KEY (emp_no) REFERENCES employees (emp_no) ON DELETE CASCADE
            );
        """))

        # Populate departments
        depts = [
            ("d001", "Marketing"),
            ("d002", "Finance"),
            ("d003", "Human Resources"),
            ("d004", "Production"),
            ("d005", "Development"),
            ("d006", "Quality Management"),
            ("d007", "Sales"),
            ("d008", "Research"),
            ("d009", "Customer Service"),
        ]
        for d in depts:
            conn.execute(text("INSERT INTO departments (dept_no, dept_name) VALUES (:no, :name)"), {"no": d[0], "name": d[1]})

        # Populate employees
        random.seed(42)
        first_names = [
            "Georgi", "Bezalel", "Parto", "Chirstian", "Kyoichi", "Anneke", "Tzvetan", "Saniya",
            "Sumant", "Duangkaew", "Mary", "Patricio", "Eberhardt", "Berni", "Guoxiang", "Kazuhito",
            "Cristinel", "Kazuhide", "Lillian", "Mayumi", "Ramzi", "Shahaf", "Bojan", "Suzette",
        ]
        last_names = [
            "Facello", "Simmel", "Bamford", "Koblick", "Maliniak", "Preusig", "Zielinski", "Kalloufi",
            "Peac", "Piveteau", "Sluis", "Bridgland", "Geraud", "Genin", "Nooteboom", "Cingiser",
        ]
        titles_list = [
            "Senior Engineer", "Staff", "Engineer", "Senior Staff", "Assistant Engineer", "Technique Leader",
        ]

        emp_count = 500
        for emp_no in range(10001, 10001 + emp_count):
            fn = random.choice(first_names)
            ln = random.choice(last_names)
            gender = random.choice(["M", "F"])
            # Hire date between 1986 and 2000
            hire_year = random.randint(1986, 2000)
            hire_month = random.randint(1, 12)
            hire_day = random.randint(1, 28)
            hire_date = f"{hire_year:04d}-{hire_month:02d}-{hire_day:02d}"
            birth_year = hire_year - random.randint(22, 35)
            birth_date = f"{birth_year:04d}-05-15"

            conn.execute(text("""
                INSERT INTO employees (emp_no, birth_date, first_name, last_name, gender, hire_date)
                VALUES (:emp_no, :bdate, :fname, :lname, :gender, :hdate)
            """), {
                "emp_no": emp_no,
                "bdate": birth_date,
                "fname": fn,
                "lname": ln,
                "gender": gender,
                "hdate": hire_date,
            })

            # Dept assignment
            dept = depts[emp_no % len(depts)][0]
            conn.execute(text("""
                INSERT INTO dept_emp (emp_no, dept_no, from_date, to_date)
                VALUES (:emp_no, :dept_no, :fdate, :tdate)
            """), {
                "emp_no": emp_no,
                "dept_no": dept,
                "fdate": hire_date,
                "tdate": "9999-01-01",
            })

            # Title
            title = titles_list[emp_no % len(titles_list)]
            conn.execute(text("""
                INSERT INTO titles (emp_no, title, from_date, to_date)
                VALUES (:emp_no, :title, :fdate, :tdate)
            """), {
                "emp_no": emp_no,
                "title": title,
                "fdate": hire_date,
                "tdate": "9999-01-01",
            })

            # Salary (some > 100,000 for Q6)
            salary = random.randint(45000, 125000)
            conn.execute(text("""
                INSERT INTO salaries (emp_no, salary, from_date, to_date)
                VALUES (:emp_no, :salary, :fdate, :tdate)
            """), {
                "emp_no": emp_no,
                "salary": salary,
                "fdate": hire_date,
                "tdate": "9999-01-01",
            })

        # Dept managers: 1 per department
        for i, d in enumerate(depts):
            mgr_emp_no = 10001 + i
            conn.execute(text("""
                INSERT INTO dept_manager (emp_no, dept_no, from_date, to_date)
                VALUES (:emp_no, :dept_no, :fdate, :tdate)
            """), {
                "emp_no": mgr_emp_no,
                "dept_no": d[0],
                "fdate": "1990-01-01",
                "tdate": "9999-01-01",
            })

    print(f"Successfully generated populated SQLite database at: {path.resolve()}")


if __name__ == "__main__":
    seed_database()
