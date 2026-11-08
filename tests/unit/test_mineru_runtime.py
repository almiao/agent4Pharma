"""同一资料库贯穿健康检查、服务控制与资料读写，且不修改父进程环境。"""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

from agent4pharma.infrastructure.llm import dependency_control as control
from agent4pharma.infrastructure.llm import mineru_figure_catalog as figures
from agent4pharma.infrastructure.llm.dependency_probe import DependencyMonitor, probe_mineru
from agent4pharma.infrastructure.llm.document_preparation import prepare_document
from agent4pharma.infrastructure.llm.mineru_document_reader import MineruDocumentReader
from agent4pharma.infrastructure.llm.mineru_runtime import mineru_environment, mineru_service_home
from agent4pharma.modules.materials.document_reading import VerifiedDocument


def test_home_is_explicit_child_environment_and_reuses_local_models(tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_HOME", "original")
    home = mineru_service_home(tmp_path)
    environment = mineru_environment(home)
    assert home == tmp_path / "services" / "mineru"
    assert environment["MINERU_HOME"] == str(home)
    assert environment["MINERU_DOCLIB_SQLITE_PATH"] == str(home / "doclib.db")
    assert environment["MINERU_MODEL_SOURCE"] == "local"
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert os.environ["MINERU_HOME"] == "original"
    assert not home.exists()


def test_all_cli_boundaries_use_same_library(tmp_path, monkeypatch):
    home = mineru_service_home(tmp_path)
    sha = "b" * 64
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["env"]["MINERU_HOME"] == str(home)
        assert kwargs["env"]["MINERU_DOCLIB_DATA_DIR"] == str(home / "doclib")
        action = command[1]
        if action == "server":
            payload = {"running": False, "parse_server": None}
        elif action == "list":
            payload = {"parses": [], "total": 0}
        elif action == "show":
            payload = {"sha256": sha, "short_id": "bbbbbbb"}
        elif action == "parse":
            payload = {"parse": {"sha256": sha, "tier": "standard", "status": "done"}}
        else:
            payload = {"sha256": sha, "short_id": "bbbbbbb", "tier": "standard", "content": "cache"}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload))

    monkeypatch.setattr(subprocess, "run", run)
    executable = Path("mineru")
    document = VerifiedDocument(Path("paper.pdf"), sha, 1)
    monitor = DependencyMonitor(executable, mineru_home=home)
    assert probe_mineru(executable, home).status == "stopped"
    assert monitor.parser_tasks().total == 0
    controller = control.DependencyController(tmp_path, monitor)
    monkeypatch.setattr(controller, "_wait_ready", Mock())
    try:
        controller._mineru("start")
    finally:
        controller.close()
    assert prepare_document(executable, document, home).status == "ready"
    assert (
        MineruDocumentReader(executable, cache_only=True, home=home).parse(document.path, sha, 1, 1).status
        == "available"
    )
    assert figures.build_figure_catalog(executable, document, tmp_path / "figures", home).pages == []
    assert any(command[1:3] == ["server", "start"] for command in calls)
