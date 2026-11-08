"""人工批准迁移的旧库备份、失败回滚及幂等。"""

import sqlite3

import pytest

from agent4pharma.composition import open_application_storage
from agent4pharma.infrastructure.persistence.sqlite import migrations


def test_v11_failure_retains_v10_and_backup_then_upgrade_is_idempotent(tmp_path, monkeypatch):
    original = migrations.MIGRATION_11
    monkeypatch.setattr(
        migrations, "MIGRATION_11", "BEGIN IMMEDIATE; CREATE TABLE transient(id); INVALID SQL;"
    )
    with pytest.raises(sqlite3.OperationalError):
        open_application_storage(tmp_path)
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        assert db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 10
        assert db.execute("SELECT name FROM sqlite_master WHERE name='transient'").fetchone() is None
    backup = next((tmp_path / "backups").glob("discovery-pre-v11-*.sqlite3"))
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 10
    monkeypatch.setattr(migrations, "MIGRATION_11", original)
    open_application_storage(tmp_path).close()
    open_application_storage(tmp_path).close()
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM schema_version WHERE version=11").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM human_package_approvals").fetchone()[0] == 0


def test_observation_cursor_preserves_interleaved_task_scope(tmp_path):
    from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import (
        ObservationRepository,
    )
    from agent4pharma.runtime.workflow_observation import WorkflowObservation

    open_application_storage(tmp_path).close()
    sink = ObservationRepository(tmp_path)
    for i in range(53):
        for subject in ("selected", "unrelated"):
            sink.append(
                WorkflowObservation(
                    id=f"{subject}-{i}",
                    subject_ref=subject,
                    stage="content",
                    operation="tool",
                    status="succeeded",
                    created_at="2026-11-02T00:00:00+00:00",
                )
            )
    first = sink.page(["selected"])
    assert len(first.items) == 50 and first.has_more
    assert first.next_cursor > len(first.items)
    second = sink.page(["selected"], first.next_cursor)
    assert len(second.items) == 3 and not second.has_more
    assert len({e.id for e in first.items + second.items}) == 53
    assert all(e.subject_ref == "selected" for e in first.items + second.items)
    assert sink.page(["selected"], second.next_cursor).items == []
