"""本地解析协议、内容绑定与读取范围的离线校验，不模拟模型质量验收。"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent4pharma.infrastructure.llm.mineru_document_reader import MineruDocumentReader
from agent4pharma.modules.materials.document_reading import BoundDocumentReader, VerifiedDocument


@pytest.fixture(autouse=True)
def cached_document_identity(monkeypatch):
    from agent4pharma.infrastructure.llm.mineru_identity import MineruIdentity

    monkeypatch.setattr(
        "agent4pharma.infrastructure.llm.mineru_document_reader.resolve_mineru_identity",
        lambda executable, sha, home: MineruIdentity(sha256=sha, short_id="abc1234"),
    )


def test_local_parse_preserves_content_and_locators_without_remote(monkeypatch):
    payload = {
        "content": {
            "sha256": "bound-hash",
            "short_id": "abc1234",
            "tier": "standard",
            "content": "doc:abc1234/tier:standard/page:1/block:2\nText",
            "content_ranges": [
                {
                    "page_range": "1",
                    "start": "doc:abc1234/tier:standard/page:1/block:2",
                    "end": "doc:abc1234/tier:standard/page:1/block:3",
                }
            ],
            "truncated": True,
            "next_request": {"locator": "doc:abc1234/tier:standard/page:2"},
        }
    }

    def run(command, **kwargs):
        assert "--remote" not in command and "--tier" not in command
        assert kwargs["env"]["MINERU_TELEMETRY_DISABLED"] == "1"
        assert kwargs["timeout"] == 10
        assert command[1] == "read"
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload["content"]))

    monkeypatch.setattr(subprocess, "run", run)
    result = MineruDocumentReader(Path("mineru")).parse(Path("paper.pdf"), "bound-hash", 1, 3)
    assert result.status == "available" and result.content.truncated
    assert result.content.next_request.locator.endswith("page:2")
    assert result.locators == [
        "doc:abc1234/tier:standard/page:1/block:2",
        "doc:abc1234/tier:standard/page:1/block:3",
    ]


@pytest.mark.parametrize(
    "response,code",
    [
        ({"error": {"code": "quality_tier_unavailable", "retryable": False}}, "quality_tier_unavailable"),
        (
            {"content": {"sha256": "other", "short_id": "123", "tier": "standard", "content": "x"}},
            "document_hash_mismatch",
        ),
        (None, "parse_pending"),
    ],
)
def test_parse_does_not_silently_fallback(monkeypatch, response, code):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(response or {}))
    )
    result = MineruDocumentReader(Path("mineru")).parse(Path("paper.pdf"), "expected", 1, 3)
    assert result.content is None and result.error_code == code


def test_bound_document_rechecks_identity_and_limits_pages():
    calls = []

    class Parser:
        def parse(self, path, sha256, start, end):
            calls.append((path, sha256, start, end))

    reader = BoundDocumentReader(lambda: VerifiedDocument(Path("bound.pdf"), "hash", 2), Parser())
    reader.read_document(1, 3)
    assert calls == [(Path("bound.pdf"), "hash", 1, 2)]
    for start, end in [(0, 1), (1, 11), (3, 3), (2, 1)]:
        with pytest.raises(ValueError):
            reader.read_document(start, end)
    assert len(calls) == 1


@pytest.mark.parametrize("tier,expected", [("standard", "available"), ("flash", "unavailable")])
def test_cache_miss_parses_once_and_never_accepts_preview_quality(monkeypatch, tier, expected):
    commands = []

    def run(command, **kwargs):
        commands.append(command[1])
        payload = (
            {"error": {"code": "not_cached"}}
            if command[1] == "read"
            else {"content": {"sha256": "hash", "short_id": "abcdef0", "tier": tier, "content": "text"}}
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(subprocess, "run", run)
    result = MineruDocumentReader(Path("mineru")).parse(Path("bound.pdf"), "hash", 1, 2)
    assert commands == ["read", "parse"]
    assert result.status == expected


def test_cached_title_page_preserves_affiliations_without_expanding_other_reads(monkeypatch):
    text = "Abstract. " * 625 + "\nAuthor affiliation: Example University."

    def run(command, **kwargs):
        assert command[1] == "read"
        limit = int(command[command.index("--limit") + 1])
        payload = {
            "sha256": "bound-hash",
            "short_id": "abc1234",
            "tier": "standard",
            "content": text[:limit],
            "truncated": len(text) > limit,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(subprocess, "run", run)
    reader = MineruDocumentReader(Path("mineru"), cache_only=True)
    first = reader.parse(Path("bound.pdf"), "bound-hash", 1, 1)
    assert first.status == "available" and not first.content.truncated
    assert first.content.content.endswith("Author affiliation: Example University.")
    later = reader.parse(Path("bound.pdf"), "bound-hash", 2, 2)
    assert later.status == "available" and later.content.truncated
    assert len(later.content.content) == 6000
