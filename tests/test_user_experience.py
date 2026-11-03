import json

import pytest
import sqlalchemy as sa

from safeslicesql.copy_evidence import inspect_copy
from safeslicesql.friendly_report import build_report, chart_data
from safeslicesql.jobs import journal
from safeslicesql.record_browser import browse_records
from safeslicesql.saved_results import load_summary, read_evidence, saved_outcome
from safeslicesql.user_errors import explain_error


@pytest.fixture
def pair():
    engines = [sa.create_engine("sqlite://") for _ in range(2)]
    for engine in engines:
        with engine.begin() as c:
            c.exec_driver_sql("CREATE TABLE department(id INTEGER PRIMARY KEY,name TEXT)")
            c.exec_driver_sql(
                "CREATE TABLE student(id INTEGER PRIMARY KEY,name TEXT,department_id INTEGER REFERENCES department(id))"
            )
            c.exec_driver_sql("INSERT INTO department VALUES(1,'Computing')")
            c.exec_driver_sql(
                "INSERT INTO student VALUES(1,'Ananya',1),(2,'Sam',NULL),(3,'100%_literal',1)"
            )
    yield engines
    for engine in engines:
        engine.dispose()


def test_search_original_and_copy_and_parent_before_after(pair):
    with pair[1].begin() as c:
        c.exec_driver_sql("DELETE FROM student WHERE id=2")
    kept = browse_records(*pair, "student", "name", "Ananya")["records"][0]
    assert kept["status"] == "Kept unchanged"
    link = kept["connections"][0]
    assert link["original"] == link["copy"] == {"id": 1, "name": "Computing"}
    absent = browse_records(*pair, "student", "id", "2", exact=True)["records"][0]
    assert absent["status"] == "Outside current copy"
    assert absent["connections"][0]["optional"]
    assert not absent["connections"][0]["required_by_copy"]
    with pair[1].begin() as c:
        c.exec_driver_sql("DELETE FROM department")
    broken = browse_records(*pair, "student", "id", "1", exact=True)["records"][0]["connections"][0]
    assert broken["required_by_copy"] and broken["copy"] is None
    assert broken["original"]["name"] == "Computing"


def test_search_literal_wildcards_sql_injection_and_paging(pair):
    assert len(browse_records(*pair, "student", "name", "%_")["records"]) == 1
    assert not browse_records(*pair, "student", "name", "' OR 1=1 --")["records"]
    with pytest.raises(ValueError):
        browse_records(*pair, "student", "name); DROP TABLE student", "x")
    for engine in pair:
        with engine.begin() as c:
            c.execute(
                sa.text("INSERT INTO student VALUES(:id,'Extra',NULL)"),
                [{"id": n} for n in range(10, 70)],
            )
    first = browse_records(*pair, "student", "name", "Extra")
    second = browse_records(*pair, "student", "name", "Extra", offset=25)
    assert first["more"] and len(first["records"]) == 25
    assert not (
        {r["keys"]["id"] for r in first["records"]} & {r["keys"]["id"] for r in second["records"]}
    )


def test_composite_key_lookup_and_copy_only_record():
    engines = [sa.create_engine("sqlite://") for _ in range(2)]
    try:
        for engine in engines:
            with engine.begin() as c:
                c.exec_driver_sql(
                    "CREATE TABLE pair(a INTEGER,b INTEGER,name TEXT,PRIMARY KEY(a,b))"
                )
                c.exec_driver_sql("INSERT INTO pair VALUES(1,2,'Matched')")
        with engines[1].begin() as c:
            c.exec_driver_sql("INSERT INTO pair VALUES(9,8,'Unexpected')")
        assert (
            browse_records(*engines, "pair", "a", "", keys={"a": 1, "b": 2})["records"][0]["status"]
            == "Kept unchanged"
        )
        assert (
            browse_records(*engines, "pair", "name", "Unexpected")["records"][0]["status"]
            == "Missing from original"
        )
    finally:
        for engine in engines:
            engine.dispose()


def test_saved_report_is_offline_and_escapes_data(pair, tmp_path):
    with pair[0].begin() as c:
        c.exec_driver_sql("UPDATE student SET name='<script>alert(1)</script>' WHERE id=1")
    proof = inspect_copy(*pair)
    proof.update(source="source", copy="target", file_input=False)
    summary = {
        "source": "source",
        "target": "target",
        "source_rows": 4,
        "copy_rows": 4,
        "created_at": "yesterday",
        "ready": True,
        "budget": 100,
        "method": "random-repair",
        "tables": proof["tables"],
        "validation": None,
        "file_input": False,
    }
    (tmp_path / "copy_summary.json").write_text(json.dumps(summary))
    (tmp_path / "copy_verification.json").write_text(json.dumps(proof))
    assert load_summary(tmp_path) == summary
    assert read_evidence(tmp_path) == proof
    html = build_report(summary, proof)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "Needs attention" in html
    assert "<svg" in html and "Original value" in html
    assert chart_data(summary, proof)["references"] == 2


def test_history_uses_current_credentials_and_journaled_target(tmp_path):
    from safeslicesql.config import AppConfig, DatabaseConfig

    folder = tmp_path / "job"
    with journal(
        folder, {"kind": "mysql", "source": "original", "budget": 25, "method": "random-repair"}
    ) as job:
        job.record["schemas"].append(
            {"name": "copy", "role": "target", "server_uuid": "expected", "state": "created"}
        )
        job.save()
    (folder / "dataset").mkdir()
    (folder / "dataset" / "dataset.json").write_text(
        json.dumps({"source": "original", "target": "copy", "tables": {"student": 3}})
    )
    config = AppConfig(
        DatabaseConfig("source-server", 3306, "u", "secret", "unused"),
        DatabaseConfig("target-server", 3306, "u", "other-secret", "unused"),
    )
    result = saved_outcome(folder, config)
    assert result.prepared.source.host == "source-server"
    assert result.prepared.target.database == "copy"
    assert result.target_server_uuid == "expected"
    assert not (folder / "run.yaml").exists()
    assert "secret" not in json.dumps(load_summary(folder))


@pytest.mark.parametrize(
    "code, expected", [(2003, "running"), (1045, "password"), (1049, "offline"), (1142, "access")]
)
def test_actionable_database_errors(code, expected):
    title, advice = explain_error(f"Database operation failed (code {code}).")
    assert title and expected in advice
