"""依赖探测只读、超时和脱敏契约。"""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent4pharma.infrastructure.llm import dependency_probe as probe


def test_mineru_timeout_is_unknown_and_never_starts_service(monkeypatch):
    run = Mock(side_effect=subprocess.TimeoutExpired("mineru", 6))
    monkeypatch.setattr(probe.subprocess, "run", run)
    status = probe.probe_mineru(Path("mineru"))
    assert status.status == "unknown"
    assert "超时" in status.detail
    assert run.call_args.args[0] == ["mineru", "server", "status", "--json"]
    assert run.call_args.kwargs["timeout"] == 6


def test_process_exists_but_parser_unhealthy_is_not_healthy(monkeypatch):
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "running": True,
                    "parse_queue_length": 2,
                    "parse_server": {"local": {"healthy": False}},
                    "recent_logs": ["private upstream diagnostics"],
                }
            ),
        )
    )
    monkeypatch.setattr(probe.subprocess, "run", run)
    monitor = probe.DependencyMonitor(Path("mineru"))
    first = monitor.snapshot(False)
    second = monitor.snapshot(True)
    assert first.items[1].status == "unavailable"
    assert first.items[1].queued == 2
    assert second.items[-1].status == "configured"
    assert "private upstream" not in second.model_dump_json()
    assert run.call_count == 1


def test_parser_tasks_filter_raw_errors_and_paths_and_report_failed_reads(monkeypatch):
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "parses": [
                        {
                            "id": 26,
                            "short_id": "b747430",
                            "tier": "standard",
                            "page_range": "1-26",
                            "status": "done",
                            "error_msg": "private",
                            "path": "private",
                        }
                    ],
                    "total": 1,
                }
            ),
        )
    )
    monkeypatch.setattr(probe.subprocess, "run", run)
    monitor = probe.DependencyMonitor(Path("mineru"))
    assert "private" not in monitor.parser_tasks().model_dump_json()
    run.return_value = subprocess.CompletedProcess([], 1, "private raw error")
    with pytest.raises(ValueError, match="查询失败"):
        monitor.parser_tasks()


def test_decider_probe_rejects_nonlocal_endpoint_without_network(monkeypatch):
    client = Mock()
    monkeypatch.setattr(probe.httpx, "Client", client)
    assert probe.probe_decider("https://example.org").status == "error"
    client.assert_not_called()


@pytest.mark.parametrize(
    "payload,returncode,expected",
    [
        ({"running": False, "parse_server": None}, 0, "stopped"),
        ({"running": True, "parse_server": {"local": {"starting": True}}}, 0, "starting"),
        ({"error": {"message": "[WinError 5] private secret path"}}, 1, "permission_error"),
        ({"error": {"message": "private secret diagnostic"}}, 1, "error"),
        ({"unexpected": True}, 0, "error"),
    ],
)
def test_mineru_distinguishes_stopped_permission_and_invalid_response(
    monkeypatch, payload, returncode, expected
):
    run = Mock(return_value=subprocess.CompletedProcess([], returncode, json.dumps(payload)))
    monkeypatch.setattr(probe.subprocess, "run", run)
    result = probe.probe_mineru(Path("mineru"))
    assert result.status == expected
    assert "private" not in result.model_dump_json()


@pytest.mark.parametrize(
    "failure,expected",
    [(probe.httpx.ConnectError("private"), "stopped"), (probe.httpx.ReadTimeout("private"), "unknown")],
)
def test_decider_refusal_and_timeout_have_different_recovery(monkeypatch, failure, expected):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.get.side_effect = failure
    monkeypatch.setattr(probe.httpx, "Client", Mock(return_value=client))
    result = probe.probe_decider("http://127.0.0.1:8895")
    assert result.status == expected
    assert "private" not in result.model_dump_json()
