"""单来源采集用例：恢复原任务窗口、推进有限页数，并记录可续跑的失败状态。"""

from datetime import datetime, timedelta

from agent4pharma.modules.collection.acquisition_interfaces import (
    CollectionPersistence,
    SourceConnector,
)
from agent4pharma.modules.collection.errors import PaginationChanged, SourceError
from agent4pharma.runtime.settings import ApplicationSettings


class CollectionService:
    """执行单来源采集与断点恢复，不负责日期调度、模型判断或最终报告。"""

    def __init__(
        self,
        config: ApplicationSettings,
        store: CollectionPersistence,
        providers: dict[str, SourceConnector],
    ):
        self.config, self.store, self.providers = config, store, providers

    def collect_source(
        self, run_id: str, batch_id: str, source: str, settings: dict, start: datetime, end: datetime
    ) -> None:
        """续跑单一来源的原窗口和游标；耗尽页预算或失败时保存 partial/failed 状态。"""
        config, store = self.config, self.store
        scope_id = f"{source}:{config.fingerprint()}"
        task = store.ensure_collection_task(scope_id, source, settings)
        days = config.overlap_days if task["last_success_at"] else config.initial_days
        left = start - timedelta(days=max(0, days - 1))
        job = store.source_job(
            run_id,
            source,
            {
                "source": source,
                "start": left.isoformat(),
                "end": end.isoformat(),
                "query": settings.get("query", ""),
            },
        )
        if job["status"] == "complete":
            return
        # Reuse the original run's window even when a newer run has advanced the collection task.
        request = job["request"]
        cursor = job["result"].get("cursor") if job["result"] else None
        store.set_job(job["id"], "running", result=job["result"], attempt=True)
        try:
            for _ in range(config.page_budget):
                page = self.providers[source].page(
                    datetime.fromisoformat(request["start"]),
                    datetime.fromisoformat(request["end"]),
                    request["query"],
                    cursor,
                )
                store.commit_page(batch_id, job["id"], page, scope_id)
                if page.deferred_error:
                    raise SourceError(page.deferred_error)
                if page.next_cursor is None:
                    break
                if page.next_cursor == cursor:
                    raise ValueError("Connector did not advance its cursor")
                cursor = page.next_cursor
            else:
                saved = store.job(job["id"])["result"]
                store.set_job(job["id"], "partial", result=saved, error="page_budget exhausted; resume")
        except Exception as exc:
            saved = store.job(job["id"])["result"]
            # Known safe errors from connectors. Never persist raw HTTP/auth exception strings.

            error = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
            status = "partial" if saved and "total" in saved else "failed"
            if isinstance(exc, PaginationChanged):
                saved = None
                status = "partial"
            store.set_job(job["id"], status, result=saved, error=error)
