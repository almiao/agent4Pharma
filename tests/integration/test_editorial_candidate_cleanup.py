"""人工清理只删除未立项候选，不触碰评估来源和已授权的制作任务。"""

import json
import sqlite3

import pytest

from agent4pharma.infrastructure.persistence.sqlite.editorial_candidate_cleanup import (
    remove_other_uncommissioned_candidates,
)


@pytest.fixture
def candidate_db(tmp_path):
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        db.executescript(
            "CREATE TABLE editorial_candidates(id TEXT,task_id TEXT);"
            "CREATE TABLE production_stories(id TEXT);"
            "CREATE TABLE production_tasks(payload TEXT);"
            "CREATE TABLE intelligence_tasks(id TEXT);"
            "INSERT INTO editorial_candidates VALUES ('keep','fulltext'),('old','abstract');"
            "INSERT INTO intelligence_tasks VALUES ('fulltext'),('abstract');"
        )
        db.execute(
            "INSERT INTO production_tasks VALUES (?)",
            (json.dumps({"status": "failed", "input_json": json.dumps({"candidates": [{"id": "keep"}]})}),),
        )
    return tmp_path


def test_cleanup_preserves_kept_task_and_source_history(candidate_db):
    assert remove_other_uncommissioned_candidates(candidate_db, "fulltext") == 1
    assert remove_other_uncommissioned_candidates(candidate_db, "fulltext") == 0
    with sqlite3.connect(candidate_db / "discovery.sqlite3") as db:
        assert db.execute("SELECT id FROM editorial_candidates").fetchall() == [("keep",)]
        assert db.execute("SELECT count(*) FROM intelligence_tasks").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM production_tasks").fetchone()[0] == 1


@pytest.mark.parametrize("reason", ["unknown", "story", "running", "other_task"])
def test_cleanup_refuses_ambiguous_or_active_production(candidate_db, reason):
    with sqlite3.connect(candidate_db / "discovery.sqlite3") as db:
        if reason == "story":
            db.execute("INSERT INTO production_stories VALUES ('existing')")
        elif reason in {"running", "other_task"}:
            task = {
                "status": "running" if reason == "running" else "failed",
                "input_json": json.dumps({"candidates": [{"id": "keep" if reason == "running" else "old"}]}),
            }
            db.execute("UPDATE production_tasks SET payload=?", (json.dumps(task),))
    with pytest.raises(ValueError):
        remove_other_uncommissioned_candidates(candidate_db, "absent" if reason == "unknown" else "fulltext")
    with sqlite3.connect(candidate_db / "discovery.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM editorial_candidates").fetchone()[0] == 2
