import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent4pharma.infrastructure.persistence.chaoslib_access import ChaosLibAccess
from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
from agent4pharma.infrastructure.web.daily_browser_downloader import DailyBrowserDownloader
from agent4pharma.infrastructure.web.daily_browser_runtime import DailyBrowserError, DailyBrowserRuntime
from agent4pharma.infrastructure.web.daily_browser_scripts import acquisition_script
from agent4pharma.modules.materials.browser_paper_service import BrowserPaperTools
from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest


def request(doi="10.1038/s41592-026-03270-z"):
    return PaperDownloadRequest(
        batch_id="batch",
        decision_id="selection:record",
        material_ref="material",
        source_id="source",
        source_record_id="record",
        title="Test paper",
        journal="Nature Methods",
        url="https://doi.org/" + doi,
        identifier=doi,
    )


def test_runtime_requires_pinned_extension_and_never_falls_back(tmp_path):
    runtime = DailyBrowserRuntime(tmp_path)
    with pytest.raises(DailyBrowserError, match="browser_runtime_not_installed"):
        runtime.command()
    package = tmp_path / "tools/browser-mcp/node_modules/@playwright/mcp/package.json"
    package.parent.mkdir(parents=True)
    package.write_text('{"version":"0.0.84"}', encoding="utf-8")
    command, args = runtime.command()
    assert "playwright" in command and "--extension" in args
    assert not {"--headless", "--isolated", "--storage-state", "--secrets"} & set(args)
    if os.name == "nt":
        assert args[args.index("--user-data-dir") + 1] == str(
            Path(os.environ["LOCALAPPDATA"]) / "Google/Chrome/User Data"
        )
    package.write_text('{"version":"0.0.85"}', encoding="utf-8")
    with pytest.raises(DailyBrowserError, match="browser_runtime_not_installed"):
        runtime.command()


def test_browser_process_uses_writable_project_temp_without_changing_host_environment(tmp_path, monkeypatch):
    import subprocess

    import playwright
    from mcp.client.stdio import get_default_environment

    invalid = str(tmp_path / "missing-host-temp")
    monkeypatch.setenv("TEMP", invalid)
    monkeypatch.setenv("PAPER_D_PASSWORD", "must-not-propagate")
    runtime = DailyBrowserRuntime(tmp_path)
    environment = runtime.environment()
    assert set(environment) == {"TEMP", "TMP", "TMPDIR"} | ({"LOCALAPPDATA"} if os.name == "nt" else set())
    assert os.environ["TEMP"] == invalid
    node = Path(playwright.__file__).parent / "driver" / ("node.exe" if os.name == "nt" else "node")
    code = """
      const fs=require('fs'),os=require('os'),path=require('path');
      const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pharma-probe-'));
      if (process.platform==='win32')
        fs.mkdirSync(path.join(process.env.LOCALAPPDATA,'ms-playwright','b'),{recursive:true});
      process.stdout.write(JSON.stringify({parent:path.dirname(dir),secretPresent:!!process.env.PAPER_D_PASSWORD}));
      fs.rmdirSync(dir);
    """
    result = subprocess.run(
        [str(node), "-e", code],
        env={**get_default_environment(), **environment},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(result.stdout) == {"parent": environment["TEMP"], "secretPresent": False}


@pytest.mark.parametrize("doi", ["10.1016/example", "10.1038/../../private", "10.1038/a';fetch('x')", ""])
def test_unsupported_or_injected_doi_never_reaches_browser(tmp_path, monkeypatch, doi):
    async def forbidden(*_):
        pytest.fail("must not connect")

    monkeypatch.setattr(DailyBrowserDownloader, "_acquire", forbidden)
    result = DailyBrowserDownloader(tmp_path).download(request(doi))
    assert result.reason == "daily_browser_publisher_not_supported"
    assert not (tmp_path / "private/chaoslib/access.json").exists()


def test_shared_account_pause_blocks_new_transport_without_resetting_counts(tmp_path, monkeypatch):
    access = ChaosLibAccess(tmp_path / "private/chaoslib")
    access.reserve()
    access.block("blocked_automation")
    before = access.status()

    async def forbidden(*_):
        pytest.fail("paused account must not connect")

    monkeypatch.setattr(DailyBrowserDownloader, "_acquire", forbidden)
    result = DailyBrowserDownloader(tmp_path).download(request())
    assert result.status == "manual_required"
    assert result.reason == "account_blocked_requires_manual"
    assert access.status() == before


def test_browser_failure_is_redacted_and_not_retried(tmp_path, monkeypatch):
    calls = []

    async def fail(*_):
        calls.append(1)
        raise RuntimeError("secret pairing URL and page content")

    monkeypatch.setattr(DailyBrowserDownloader, "_acquire", fail)
    tools = BrowserPaperTools(request(), DailyBrowserDownloader(tmp_path), PaperDownloadRepository(tmp_path))
    assert tools.get_browser_paper_status().status == "not_started"
    assert calls == []
    assert tools.acquire_browser_paper().reason == "browser_transport_failed"
    assert tools.acquire_browser_paper().reason == "browser_transport_failed"
    assert calls == [1]
    assert "secret" not in tools.get_browser_paper_status().model_dump_json()


def test_saved_file_is_not_success_until_pdf_identity_validation(tmp_path, monkeypatch):
    async def pretend_saved(*_):
        return {"status": "file_saved"}

    monkeypatch.setattr(DailyBrowserDownloader, "_acquire", pretend_saved)
    result = DailyBrowserDownloader(tmp_path).download(request())
    assert result.status == "failed" and result.reason == "browser_file_missing"


def test_default_download_has_no_daily_cap_and_preserves_interval(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from agent4pharma.infrastructure.persistence import chaoslib_access

    now = datetime(2026, 11, 1, 12, tzinfo=UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(chaoslib_access, "datetime", FixedDatetime)
    guard = ChaosLibAccess(tmp_path / "private/chaoslib")
    for seconds in (600, 480, 360, 240, 120):
        assert not guard.reserve(now=now - timedelta(seconds=seconds))
    calls = []

    async def stop(self, *_):
        assert not self._reserve_publisher_attempt()
        calls.append(1)
        return {"status": "publisher_verification_required"}

    monkeypatch.setattr(DailyBrowserDownloader, "_acquire", stop)
    assert (
        DailyBrowserDownloader(tmp_path, daily_attempt_limit=5).download(request()).reason
        == "daily_attempt_limit"
    )
    granted = DailyBrowserDownloader(tmp_path).download(request())
    assert granted.reason == "publisher_verification_required"
    assert guard.status()["count"] == 6 and calls == [1]
    assert DailyBrowserDownloader(tmp_path).download(request()).reason == "attempt_interval"
    assert (
        DailyBrowserDownloader(tmp_path, daily_attempt_limit=6).download(request()).reason
        == "daily_attempt_limit"
    )
    assert calls == [1]


def test_execute_checks_protocol_and_returns_only_json_result(tmp_path):
    class Client:
        async def call_tool(self, name, arguments):
            assert name == "browser_run_code_unsafe"
            return SimpleNamespace(
                is_error=False,
                content=[
                    SimpleNamespace(type="text", text='### Result\n{"status":"ready"}\n### Page\nprivate')
                ],
            )

    result = asyncio.run(DailyBrowserRuntime(tmp_path).execute(Client(), "fixed host code"))
    assert result == {"status": "ready"}


def test_waiting_for_connection_does_not_consume_download_budget(tmp_path):
    class Disconnected:
        def check(self, *args, **kwargs):
            assert kwargs["allow_connect"] is False
            raise DailyBrowserError("browser_connection_required")

    result = DailyBrowserDownloader(tmp_path, connection=Disconnected()).download(request())
    assert result.reason == "browser_connection_required"
    assert not (tmp_path / "private/chaoslib/access.json").exists()


def test_publisher_budget_is_reserved_after_ready_and_before_script_execution(tmp_path):
    guard = ChaosLibAccess(tmp_path / "private/chaoslib")
    events = []

    class Ready:
        def check(self, *args, **kwargs):
            assert guard.status() == {}
            events.append("ready")
            return "ready"

        def execute(self, code, **kwargs):
            assert kwargs["allow_connect"] is False
            assert guard.status()["count"] == 1
            events.append("publisher")
            return {"status": "publisher_verification_required"}

    result = DailyBrowserDownloader(tmp_path, connection=Ready()).download(request())
    assert result.reason == "publisher_verification_required"
    assert guard.status()["count"] == 1 and events == ["ready", "publisher"]


def test_unanswered_extension_connection_has_bounded_wait_and_cancels_tool(tmp_path):
    cancelled = []

    class Client:
        async def call_tool(self, name, arguments):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)

    with pytest.raises(DailyBrowserError, match="browser_connection_or_action_timeout"):
        asyncio.run(DailyBrowserRuntime(tmp_path).execute(Client(), "fixed", timeout_seconds=0.01))
    assert cancelled == [True]


@pytest.mark.parametrize("host", ["[::1]", "127.0.0.1", "outside.example"])
def test_manual_connection_notice_only_exposes_loopback_pairing_link(tmp_path, capsys, host):
    notices = asyncio.Queue()
    endpoint = f"ws://{host}:1234/extension/12345678-1234-1234-1234-123456789abc"
    notices.put_nowait("private page metadata\nextension endpoint: " + endpoint + ".")

    async def publish():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(DailyBrowserRuntime(tmp_path)._publish_connect_url(notices), 0.02)

    asyncio.run(publish())
    output = capsys.readouterr()
    assert not output.out and "private page metadata" not in output.err
    if host == "outside.example":
        assert not output.err
    else:
        from urllib.parse import parse_qs, urlsplit

        query = parse_qs(urlsplit(output.err.splitlines()[-1]).query)
        assert query["mcpRelayUrl"] == [endpoint]
        assert "token" not in query


def test_runtime_closes_stdio_without_persisting_private_diagnostics(tmp_path, monkeypatch):
    # Real stdio transport against an explicitly offline test server; never opens a browser.
    code = """
from mcp.server.mcpserver import MCPServer
server=MCPServer('offline-browser-runtime-test')
@server.tool()
def browser_run_code_unsafe(code:str)->str:
    return 'offline only'
server.run(transport='stdio')
"""
    monkeypatch.setattr(DailyBrowserRuntime, "command", lambda _: (sys.executable, ["-c", code]))

    async def probe():
        async with DailyBrowserRuntime(tmp_path).session():
            assert not list((tmp_path / "private/chaoslib").glob("connection-*.log"))

    asyncio.run(probe())
    assert not list((tmp_path / "private/chaoslib").glob("connection-*.log"))


def test_persistent_stderr_filter_discards_raw_page_and_credential_diagnostics():
    from io import StringIO

    from agent4pharma.infrastructure.web.daily_browser_runtime import _drain_relay_notices

    notices = []
    endpoint = (
        "pw:mcp:relay CDP relay server started, extension endpoint: "
        "ws://127.0.0.1:1234/extension/12345678-1234-1234-1234-123456789abc"
    )
    _drain_relay_notices(
        StringIO("secret browser credential\n" + endpoint + "\nprivate page body\n" + endpoint + "\n"),
        notices.append,
    )
    assert notices == [endpoint + "\n"]


def test_relay_filter_accepts_host_disconnect_but_rejects_page_message_impersonation():
    from io import StringIO

    from agent4pharma.infrastructure.web.daily_browser_runtime import _drain_relay_notices

    notices = []
    fake = 'pw:mcp:relay received event: {"message":"pw:mcp:relay closing extension connection: fake"}\n'
    real = "2026-10-31T19:00:00.000Z pw:mcp:relay closing extension connection: Browser closed\n"
    _drain_relay_notices(StringIO(fake + real), notices.append)
    assert notices == ["browser_disconnected"]


def test_bound_mcp_exposes_no_url_script_path_or_retry_arguments(tmp_path, monkeypatch):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.modules.discovery.intelligence_contracts import (
        IntelligenceEvidence,
        IntelligencePolicy,
        IntelligencePolicyVersion,
        IntelligenceRequest,
    )
    from agent4pharma.modules.discovery.paper_selection import PaperSelectionItem, PaperSelectionRun

    run = PaperSelectionRun(
        id="selection-" + "b" * 24,
        batch_id="batch",
        phase="fulltexts",
        created_at="2026-11-01T00:00:00Z",
        items=[
            PaperSelectionItem(
                rank=1,
                state="shortlisted",
                request=IntelligenceRequest(
                    material_ref="material",
                    model="offline",
                    policy=IntelligencePolicyVersion(
                        id="policy",
                        version=1,
                        status="published",
                        content_hash="hash",
                        definition=IntelligencePolicy(instructions="Offline fixture screening rules " * 3),
                    ),
                    evidence=[
                        IntelligenceEvidence(
                            id="record", title="Test paper", text="", doi=request().identifier
                        )
                    ],
                ),
            )
        ],
    )
    PaperSelectionRepository(tmp_path).save(run)

    async def probe():
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "agent4pharma",
                "browser-paper-mcp",
                "--root",
                str(tmp_path),
                "--run",
                run.id,
                "--record",
                "record",
            ],
        )
        async with stdio_client(params) as streams:
            async with ClientSession(*streams, read_timeout_seconds=15) as client:
                await client.initialize()
                listing = await client.list_tools()
                assert {t.name for t in listing.tools} == {
                    "get_browser_paper_status",
                    "acquire_browser_paper",
                }
                assert all(not t.input_schema.get("properties") for t in listing.tools)
                result = await client.call_tool("get_browser_paper_status", {})
                assert not result.is_error
                assert "not_started" in str(result.content)

    asyncio.run(probe())
    assert not (tmp_path / "private/chaoslib/access.json").exists()
    from agent4pharma.composition import build_browser_paper_tools

    run.phase = "abstracts"
    PaperSelectionRepository(tmp_path).save(run)
    with pytest.raises(ValueError, match="摘要筛选"):
        build_browser_paper_tools(tmp_path, run.id, "record")


@pytest.mark.parametrize("redirect", [False, True])
@pytest.mark.parametrize("mode", ["reuse", "nested", "multiple"])
def test_browser_script_stops_at_unapproved_redirect_or_missing_pdf(tmp_path, redirect, mode):
    # Execute the actual generated JS against an offline browser stub; no website or model calls.
    import subprocess

    import playwright

    node = Path(playwright.__file__).parent / "driver" / ("node.exe" if os.name == "nt" else "node")
    code = acquisition_script(request(), tmp_path / 'quoted"path.pdf')
    harness = """
      const vm = require('vm');
      let url = 'https://nature.66557.net/', lookedForPdf = false, entryClicks = 0;
      const mode = MODE;
      const gate = {isClosed: () => false, opener: async () => page, url: () => 'https://www.chaoslib.com/confirm'};
      const paper = {
        isClosed: () => false, opener: async () => mode === 'nested' ? gate : page,
        url: () => url, waitForLoadState: async () => {}, waitForURL: async () => {},
        goto: async () => {url = REDIRECT ? 'https://unapproved.example/' : 'https://nature.66557.net/articles/test'},
        getByText: () => ({count: async () => 0}),
        getByRole: () => {lookedForPdf = true; return {first: () => ({count: async () => 0})}}
      };
      const page = {
        url: () => 'https://www.chaoslib.com/',
        locator: () => ({count: async () => 0}),
        getByText: () => ({click: async () => {}}),
        getByRole: () => ({count: async () => 1, isEnabled: async () => true,
          click: async () => {entryClicks++}}),
        context: () => ({pages: () => mode === 'nested' && !entryClicks ? [page] :
          mode === 'multiple' ? [page, paper, {...paper}] : [page, gate, paper]}),
        waitForEvent: async () => paper
      };
      const fn = vm.runInNewContext('(' + process.argv[1] + ')', {URL, setTimeout: fn => fn()});
      fn(page).then(result => console.log(JSON.stringify({result, lookedForPdf, entryClicks})));
    """.replace("REDIRECT", json.dumps(redirect)).replace("MODE", json.dumps(mode))
    result = subprocess.run(
        [str(node), "-e", harness, code], capture_output=True, text=True, timeout=5, check=True
    )
    observed = json.loads(result.stdout)
    assert observed == {
        "result": {
            "status": "publisher_tabs_require_human"
            if mode == "multiple"
            else "publisher_origin_requires_human"
            if redirect
            else "pdf_link_requires_human"
        },
        "lookedForPdf": not redirect and mode != "multiple",
        "entryClicks": 1 if mode == "nested" else 0,
    }
