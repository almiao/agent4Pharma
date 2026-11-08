"""消费 DSH 流事件，仅落盘数字用量与搜索出处，不保存隐藏推理或请求凭证。"""

import hashlib
from collections.abc import Callable

from pydantic import BaseModel, Field, JsonValue, ValidationError

from agent4pharma.modules.content.draft_contracts import ResearchEvidence
from agent4pharma.runtime.production_usage import ProductionUsage
from agent4pharma.runtime.workflow_observation import ObservationSink, WorkflowObservation
from agent4pharma.shared.clock import utcnow


class _Usage(BaseModel):
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)
    totalTokens: int | None = Field(default=None, ge=0)
    cacheReadTokens: int | None = Field(default=None, ge=0)
    cacheWriteTokens: int | None = Field(default=None, ge=0)


class ProductionTelemetry:
    """逐请求结算立即保存，父进程硬超时仍能看到已完成请求的用量。"""

    def __init__(
        self,
        sink: ObservationSink,
        task_id: str,
        role: str,
        attempt: int,
        save_search: Callable[[list[ResearchEvidence]], None] | None = None,
    ) -> None:
        self.sink, self.task_id, self.role, self.attempt = sink, task_id, role, attempt
        self.save_search = save_search
        self.samples: dict[str, _Usage] = {}
        self.retry = 0
        self.compactions = 0
        self.search_calls = 0
        self.calls: dict[str, str] = {}
        self.revision = 0
        self.complete = False

    def event(self, event: dict[str, JsonValue]) -> None:
        """只理解已核对的 DSH 事件结构；未知事件忽略而不猜测字段含义。"""
        kind, data = event.get("type"), event.get("data")
        if not isinstance(data, dict):
            return
        if kind == "turn/start":
            self.complete = False
        if kind == "llm/retry-started":
            self.retry += 1
        elif kind in {"assistant/message", "assistant/attempt", "compaction/summary"}:
            value = data.get("usage")
            stream = data.get("stream", [])
            if value is None and isinstance(stream, list):
                for part in stream:
                    if isinstance(part, dict) and part.get("type") == "usage":
                        value = part.get("usage")
            if kind == "compaction/summary":
                self.compactions += 1
            if value is not None:
                try:
                    sample = _Usage.model_validate(value)
                except ValidationError:
                    return
                key = f"{data.get('turn')}:{data.get('step')}:{self.retry}"
                if kind == "compaction/summary":
                    key = f"compaction:{self.compactions}"
                self.samples[key] = sample
            self.persist()
        elif kind == "tool/call" and data.get("name") == "web_search":
            self.calls[str(data.get("callId"))] = "web_search"
            self.search_calls += 1
            self.persist()
        elif kind == "tool/result" and self.save_search:
            self._search_result(data)
        elif kind == "turn/end":
            self.complete = True
            self.persist()

    def _search_result(self, data: dict[str, JsonValue]) -> None:
        message, meta = data.get("message"), data.get("meta")
        if not isinstance(message, dict) or not isinstance(meta, dict) or message.get("isError"):
            return
        if str(message.get("toolCallId", message.get("callId"))) not in self.calls:
            return
        sources = meta.get("sources")
        if not isinstance(sources, list):
            return
        evidence = []
        for source in sources[:5]:
            if not isinstance(source, dict):
                continue
            url, title, snippet = source.get("url"), source.get("title"), source.get("snippet")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                continue
            text = (snippet if isinstance(snippet, str) and snippet else str(title or url))[:800]
            evidence.append(
                ResearchEvidence(
                    id="web-" + hashlib.sha256((url + text).encode()).hexdigest()[:24],
                    title=str(title or url)[:500],
                    text=text,
                    url=url,
                    origin="web_search_snippet",
                    read_scope="DSH web_search 实际返回的检索片段；未取得论文全文",
                )
            )
        if evidence:
            self.save_search(evidence)

    def persist(self) -> None:
        """追加可重放快照；只累计每次请求的最后一份供应商结算。"""
        samples = list(self.samples.values())

        def total(field: str) -> int | None:
            values = [getattr(sample, field) for sample in samples]
            return sum(values) if values and all(v is not None for v in values) else None

        last = samples[-1] if samples else None
        view = ProductionUsage(
            attempt=self.attempt,
            requests=len(samples),
            uncached_input_tokens=total("inputTokens") or 0,
            output_tokens=total("outputTokens") or 0,
            cache_read_tokens=total("cacheReadTokens"),
            cache_write_tokens=total("cacheWriteTokens"),
            total_tokens=total("totalTokens"),
            last_input_tokens=last.totalTokens - last.outputTokens
            if last and last.totalTokens is not None
            else None,
            compactions=self.compactions,
            search_calls=self.search_calls,
            complete=self.complete,
            updated_at=utcnow(),
        )
        self.revision += 1
        self.sink.append(
            WorkflowObservation(
                id=f"usage-{self.task_id}-{self.attempt}-{self.revision}",
                subject_ref=self.task_id,
                stage=self.role,
                operation="token_usage",
                status="succeeded",
                output_json=view.model_dump_json(),
                created_at=view.updated_at,
            )
        )
