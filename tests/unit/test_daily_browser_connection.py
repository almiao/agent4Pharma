import json
import sys

import pytest

from agent4pharma.infrastructure.web.daily_browser_connection import DailyBrowserConnection
from agent4pharma.infrastructure.web.daily_browser_runtime import (
    DailyBrowserError,
    DailyBrowserRuntime,
    browser_failure_reason,
)


def test_connection_reuses_one_real_stdio_process_and_closes_owner_resources(tmp_path, monkeypatch):
    # Actual SDK transport against an offline MCP server; it does not open Chrome or access a site.
    code = """
import json,os
from mcp.server.mcpserver import MCPServer
server=MCPServer('offline-persistent-browser')
@server.tool()
def browser_run_code_unsafe(code:str)->str:
    return '### Result\\n'+json.dumps({'status':'ready','pid':os.getpid()})
server.run(transport='stdio')
"""
    monkeypatch.setattr(DailyBrowserRuntime, "command", lambda _: (sys.executable, ["-c", code]))
    connection = DailyBrowserConnection(tmp_path)
    try:
        assert connection.status().phase == "disconnected"
        assert connection.connect().phase == "ready"
        first = connection.execute("first fixed script")
        second = connection.execute("second fixed script", allow_connect=False)
        assert first["pid"] == second["pid"]
        assert connection.check() == "ready"
        assert not list((tmp_path / "private/chaoslib").glob("connection-*.log"))
        assert not (tmp_path / "private/chaoslib/access.json").exists()
    finally:
        connection.close()
    assert connection.status().phase == "disconnected"
    assert not connection._thread.is_alive()
    assert not list((tmp_path / "private/chaoslib").glob("connection-*.log"))
    with pytest.raises(DailyBrowserError, match="browser_connection_closed"):
        connection.execute("cannot restart after close")


def test_grouped_transport_error_preserves_only_fixed_reason():
    grouped = ExceptionGroup(
        "private diagnostic",
        [
            ValueError("secret browser details"),
            ExceptionGroup("nested", [DailyBrowserError("browser_connection_or_action_timeout")]),
        ],
    )
    assert browser_failure_reason(grouped) == "browser_connection_or_action_timeout"
    assert browser_failure_reason(ValueError("secret")) == "browser_transport_failed"
    assert "secret" not in json.dumps(browser_failure_reason(grouped))


def test_download_cannot_implicitly_start_new_browser_authorization(tmp_path):
    connection = DailyBrowserConnection(tmp_path)
    try:
        with pytest.raises(DailyBrowserError, match="browser_connection_required"):
            connection.check(allow_connect=False)
        assert connection._thread is None
        assert connection.status().phase == "disconnected"
        assert not (tmp_path / "private/chaoslib").exists()
    finally:
        connection.close()


def test_late_connection_notice_cannot_replace_ready_state(tmp_path):
    connection = DailyBrowserConnection(tmp_path)
    connection._set("ready")
    connection._notice("chrome-extension://ignored-late-notice")
    assert connection.status().phase == "ready"
    assert not connection.status().connect_url


def test_disconnect_during_check_cannot_be_overwritten_by_stale_ready_reply(tmp_path, monkeypatch):
    connection = DailyBrowserConnection(tmp_path)
    connection._set("ready")

    def reply(*args, **kwargs):
        connection._disconnected()
        return {"status": "ready"}

    monkeypatch.setattr(connection, "execute", reply)
    assert connection.check(allow_connect=False) == "browser_connection_required"
    assert connection.status().phase == "error"
