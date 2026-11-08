"""来源当前可用性与历史采集事实、关注状态相互独立。"""

from datetime import datetime, timedelta, timezone

import pytest

from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.infrastructure.persistence.sqlite.management_sources import ManagedSourceRepository
from agent4pharma.modules.sources.management import (
    SourceAccessCheck,
    SourceCollectionAvailability,
    SourceEdit,
)


def test_recheck_persists_without_enabling_and_recovery_replaces_label(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    repository = ManagedSourceRepository(tmp_path)
    source = repository.save_source(
        SourceEdit(name="Journal", endpoint_url="https://example.org/rss", status="paused")
    )
    now = datetime.now(timezone.utc)
    blocked = SourceCollectionAvailability(
        status="currently_unavailable",
        reason="HTTP 403",
        checked_at=now,
        checks=[
            SourceAccessCheck(
                url=source.endpoint_url,
                checked_at=now,
                mode="configured_entry",
                outcome="http_error",
                http_status=403,
            )
        ],
    )
    repository.record_collection_availability(source.id, blocked, source.revision)
    updated = repository.list_sources(source.id)[0]
    assert updated.collection_availability == blocked
    assert updated.status == "paused"
    assert store.db.execute("SELECT enabled FROM source_endpoints").fetchone()[0] == 0
    with pytest.raises(ValueError, match="已被修改"):
        repository.record_collection_availability(source.id, blocked, source.revision)
    newer = blocked.model_copy(
        update={"status": "available", "reason": "RSS 已恢复", "checked_at": now + timedelta(seconds=1)}
    )
    repository.record_collection_availability(source.id, newer, updated.revision)
    updated = repository.list_sources(source.id)[0]
    assert updated.collection_availability.status == "available"
    with pytest.raises(ValueError, match="较早"):
        repository.record_collection_availability(source.id, blocked, updated.revision)
    repository.save_source(SourceEdit.model_validate(updated.model_dump()), source.id)
    assert repository.list_sources(source.id)[0].collection_availability == newer
    assert (
        store.db.execute(
            "SELECT count(*) FROM maintenance_audit WHERE action='collection_recheck'"
        ).fetchone()[0]
        == 2
    )
    with pytest.raises(KeyError):
        repository.record_collection_availability("missing", blocked, 0)
    store.close()
