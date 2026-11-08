import json

from agent4pharma.infrastructure.llm.patch import create_patch
from agent4pharma.infrastructure.llm.production_telemetry import ProductionTelemetry
from agent4pharma.runtime.production_usage import summarize_usage
from agent4pharma.runtime.settings import ApplicationSettings


class MemorySink:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)


def test_usage_deduplicates_final_report_but_counts_retry_and_compaction():
    sink = MemorySink()
    meter = ProductionTelemetry(sink, "task", "content", 2)
    usage = dict(inputTokens=10, outputTokens=5, totalTokens=115, cacheReadTokens=100)
    data = dict(turn=1, step=1, usage=usage, hidden_reasoning="must not persist")
    meter.event(dict(type="assistant/attempt", data=data))
    meter.event(dict(type="assistant/message", data=data))
    assert summarize_usage(sink.events).reported_total_tokens == 115
    meter.event(dict(type="llm/retry-started", data={}))
    meter.event(dict(type="assistant/message", data=data))
    meter.event(dict(type="compaction/summary", data=dict(usage=usage)))
    meter.event(dict(type="turn/end", data={}))
    view = summarize_usage(sink.events)
    row = view.attempts[0]
    assert view.reported_total_tokens == 345
    assert (row.requests, row.compactions, row.cache_read_tokens, row.uncached_input_tokens) == (
        3,
        1,
        300,
        30,
    )
    assert row.complete and row.last_input_tokens == 110
    assert "must not persist" not in "".join(e.output_json for e in sink.events)


def test_missing_usage_is_unknown_and_search_requires_actual_call():
    sink, sources = MemorySink(), []
    meter = ProductionTelemetry(sink, "task", "content", 1, sources.extend)
    data = dict(
        message=dict(toolCallId="call", isError=False),
        meta=dict(
            sources=[
                dict(url="https://example.org/paper", title="Research", snippet="Public source snippet."),
                dict(url="file:///private", snippet="untrusted"),
            ]
        ),
    )
    meter.event(dict(type="tool/result", data=data))
    assert sources == []
    meter.event(dict(type="tool/call", data=dict(name="web_search", callId="call")))
    meter.event(dict(type="tool/result", data=data))
    assert len(sources) == 1 and not sources[0].fulltext
    assert sources[0].origin == "web_search_snippet"
    meter.event(
        dict(
            type="assistant/message",
            data=dict(turn=1, step=1, stream=[dict(type="usage", usage=dict(inputTokens=3, outputTokens=2))]),
        )
    )
    view = summarize_usage(sink.events)
    assert view.reported_total_tokens is None
    assert view.attempts[0].search_calls == 1
    assert not view.attempts[0].complete
    assert summarize_usage([]).reported_total_tokens is None


def test_search_and_compaction_are_explicit_patch_capabilities(tmp_path):
    config = ApplicationSettings(data_dir=tmp_path)

    def patch(**kwargs):
        path = create_patch(tmp_path / "config.toml", config, "task", "prompt", mcp_args=["bound"], **kwargs)
        return json.loads(path.read_text(encoding="utf-8"))

    default = patch()
    assert not any(r.get("id") == "compaction-basic" for r in default)
    active = patch(web_search=True, newsroom_context=True)
    plugins = [p for r in active for p in r.get("insert", [])]
    web = next(p for p in plugins if p["id"] == "newsroom-web-tools")["config"]
    assert web["fetch"] is False and web["searchMaxQueries"] == 1 and web["searchMaxResults"] == 5
    assert next(r for r in active if r.get("id") == "compaction-basic")["config"]["maxTokens"] == 4096
    research = patch(newsroom_context=True, research_context=True)
    compaction = next(r["config"] for r in research if r.get("id") == "compaction-basic")
    assert compaction["thresholdRatio"] == 0.06 and compaction["retainRatio"] == 0.02
