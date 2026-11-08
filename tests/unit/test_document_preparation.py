import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent4pharma.infrastructure.llm.document_preparation import prepare_document
from agent4pharma.infrastructure.llm.mineru_document_reader import MineruDocumentReader
from agent4pharma.infrastructure.llm.mineru_identity import resolve_mineru_identity
from agent4pharma.modules.materials.document_reading import VerifiedDocument


def test_whole_document_preparation_is_nonblocking_and_identity_checked(monkeypatch):
    document = VerifiedDocument(path=Path("paper.pdf"), sha256="b" * 64, pages=23)

    def run(command, **kwargs):
        assert command[command.index("--pages") + 1] == "all"
        assert "--no-wait" in command and "--force" not in command and "--remote" not in command
        assert kwargs["timeout"] == 20
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "parse": {
                        "sha256": document.sha256,
                        "tier": "standard",
                        "status": "done",
                    }
                }
            ),
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert prepare_document(Path("mineru"), document).status == "ready"
    assert prepare_document(None, document).status == "unavailable"


def test_cache_only_read_never_starts_parser_on_cache_miss(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[1] == "show":
            return SimpleNamespace(
                returncode=0, stdout=json.dumps({"sha256": "b" * 64, "short_id": "bbbbbbb123"})
            )
        assert command[2] == "doc:bbbbbbb123/tier:standard/page:1-3"
        return SimpleNamespace(returncode=1, stdout=json.dumps({"error": {"code": "doc_not_found"}}))

    monkeypatch.setattr(subprocess, "run", run)
    result = MineruDocumentReader(Path("mineru"), cache_only=True).parse(Path("paper.pdf"), "b" * 64, 1, 3)
    assert result.status == "pending"
    assert len(calls) == 2 and [c[1] for c in calls] == ["show", "read"]


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("quality_tier_unavailable", "pending"),
        ("server_busy", "pending"),
        ("server_not_running", "unavailable"),
        ("invalid_request", "failed"),
    ],
)
def test_preparation_distinguishes_temporary_service_errors(monkeypatch, code, status):
    document = VerifiedDocument(path=Path("paper.pdf"), sha256="b" * 64, pages=23)
    response = SimpleNamespace(
        returncode=1, stdout=json.dumps({"error": {"code": code, "message": "private detail"}})
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: response)
    preparation = prepare_document(Path("mineru"), document)
    assert preparation.status == status
    assert "private detail" not in preparation.model_dump_json()


def test_document_lookup_rejects_wrong_hash_before_reading(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"sha256": "c" * 64, "short_id": "ccccccc"}))

    monkeypatch.setattr(subprocess, "run", run)
    result = MineruDocumentReader(Path("mineru"), cache_only=True).parse(Path("paper.pdf"), "b" * 64, 1, 3)
    assert result.error_code == "document_hash_mismatch"
    assert len(calls) == 1 and calls[0][1] == "show"


def test_document_lookup_timeout_does_not_start_parser(monkeypatch):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", run)
    result = resolve_mineru_identity(Path("mineru"), "b" * 64)
    assert result.status == "pending" and result.error_code == "document_lookup_timeout"


def test_document_lookup_preserves_service_error_without_leaking_diagnostics(monkeypatch):
    def run(command, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                {"error": {"code": "server_not_running", "message": "private path and secret"}}
            ),
        )

    monkeypatch.setattr(subprocess, "run", run)
    result = resolve_mineru_identity(Path("mineru"), "b" * 64)
    assert result.status == "unavailable" and result.error_code == "server_not_running"
    assert "secret" not in result.model_dump_json()
