"""来源筛选覆盖完整目录，分页与可用性不混淆关注状态。"""

from contextlib import closing
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent4pharma.entrypoints.web.api import WorkbenchAPI
from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.infrastructure.persistence.sqlite.management_sources import ManagedSourceRepository
from agent4pharma.modules.sources.management import (
    SourceAccessCheck,
    SourceCollectionAvailability,
    SourceDirectoryFilters,
    SourceEdit,
)


def test_filters_precede_pagination_and_remain_independent(tmp_path):
    with closing(SqliteApplicationStorage(tmp_path)) as storage:
        storage.db.executemany(
            "INSERT INTO sources(id,descriptor,name,source_kind,status) VALUES (?,?,?,?,?)",
            [(f"news-{i:03}", "{}", f"A{i:03}", "news_site", "watch") for i in range(205)],
        )
        storage.db.commit()
        repository = ManagedSourceRepository(tmp_path)
        target = repository.save_source(
            SourceEdit(
                name="Z Journal", source_kind="journal", status="paused", homepage_url="https://example.org"
            )
        )
        now = datetime.now(timezone.utc)
        repository.record_collection_availability(
            target.id,
            SourceCollectionAvailability(
                status="available",
                reason="RSS 已验证",
                checked_at=now,
                checks=[
                    SourceAccessCheck(
                        url="https://example.org/rss", checked_at=now, mode="configured_entry", outcome="feed"
                    )
                ],
            ),
            target.revision,
        )
        api = WorkbenchAPI(SimpleNamespace(sources=repository, cases=SimpleNamespace(root=tmp_path)))
        first = api.get("/api/sources")
        assert len(first["items"]) == 200 and first["has_more"]
        last = api.get("/api/sources?offset=200")
        assert len(last["items"]) == 6 and not last["has_more"]
        assert {s["id"] for s in first["items"]}.isdisjoint(s["id"] for s in last["items"])
        filtered = api.get("/api/sources?availability=available&kind=journal&status=paused&q=example.org")
        assert [s["id"] for s in filtered["items"]] == [target.id]
        assert filtered["kinds"] == ["journal", "news_site"]
        assert not api.get("/api/sources?availability=available&status=watch")["items"]
        assert not api.get("/api/sources?q=A000&kind=journal")["items"]
        assert not api.get("/api/sources?availability=currently_unavailable")["items"]
        unverified = api.get("/api/sources?availability=unverified&offset=200")
        assert len(unverified["items"]) == 5 and not unverified["has_more"]
        assert not repository.list_sources(filters=SourceDirectoryFilters(kind="' OR 1=1 --"))


@pytest.mark.parametrize("query", ["offset=-1", "offset=wrong", "availability=wrong", "status=wrong"])
def test_invalid_directory_filters_are_rejected(query, tmp_path):
    api = WorkbenchAPI(SimpleNamespace(cases=SimpleNamespace(root=tmp_path)))
    with pytest.raises(ValidationError):
        api.get("/api/sources?" + query)
