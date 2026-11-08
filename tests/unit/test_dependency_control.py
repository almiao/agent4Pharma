"""本机服务控制的权限、并发、进程归属与失败语义；默认不启动真实依赖。"""

import subprocess
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, Mock

import pytest
from pydantic import ValidationError

from agent4pharma.infrastructure.llm import dependency_control as control
from agent4pharma.infrastructure.llm.dependency_probe import DependencyMonitor
from agent4pharma.runtime.dependency_status import DependencyControlRequest, DependencyStatus


def status(healthy=False):
    return DependencyStatus(
        id="decider", title="Decider", status="healthy" if healthy else "unknown", detail="test"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"service": "backend", "action": "start"},
        {"service": "decider", "action": "restart"},
        {"service": "mineru", "action": "start", "command": "arbitrary"},
    ],
)
def test_only_fixed_services_and_confirmed_restart_are_accepted(payload):
    with pytest.raises(ValidationError):
        DependencyControlRequest.model_validate(payload)


def test_inflight_operation_is_reused_and_reading_never_starts(tmp_path, monkeypatch):
    controller = control.DependencyController(tmp_path, DependencyMonitor(None))
    entered, release = Event(), Event()

    def start(action):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(controller, "_mineru", start)
    monkeypatch.setattr(control, "probe_mineru", lambda *args: status(True))
    request = DependencyControlRequest(service="mineru", action="start")
    try:
        controller._decorate(status())
        assert not entered.is_set()
        first = controller.control(request)
        assert entered.wait(2)
        assert controller.control(request).id == first.id
        assert first.status == "running"
    finally:
        release.set()
        controller.close()


def test_mineru_uses_official_command_and_requires_real_health(tmp_path, monkeypatch):
    controller = control.DependencyController(tmp_path, DependencyMonitor(Path("mineru")))
    run = Mock(return_value=subprocess.CompletedProcess([], 0, b"private diagnostics"))
    monkeypatch.setattr(control.subprocess, "run", run)
    monkeypatch.setattr(control, "probe_mineru", lambda *args: status())
    wait = Mock(side_effect=control.DependencyControlError("health failed"))
    monkeypatch.setattr(controller, "_wait_ready", wait)
    try:
        with pytest.raises(control.DependencyControlError, match="health failed"):
            controller._mineru("start")
        assert run.call_args.args[0] == ["mineru", "server", "start"]
        assert run.call_args.kwargs["timeout"] == 90
    finally:
        controller.close()


def test_decider_launch_is_offline_bound_and_owned_restart_only(tmp_path, monkeypatch):
    controller = control.DependencyController(
        tmp_path / "data", DependencyMonitor(None, "http://127.0.0.1:8895")
    )
    executable, script, checkpoint = controller._decider_paths()
    for path in (executable, script, checkpoint / "installation.json"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    process = Mock(pid=12345)
    process.poll.return_value = None
    launch = Mock(return_value=process)
    monkeypatch.setattr(control.subprocess, "Popen", launch)
    monkeypatch.setattr(control.socket, "create_connection", Mock(side_effect=OSError("not listening")))
    monkeypatch.setattr(control.socket, "socket", MagicMock())
    monkeypatch.setattr(control, "probe_decider", lambda *args: status())
    monkeypatch.setattr(controller, "_wait_ready", Mock())
    try:
        with pytest.raises(control.DependencyControlError, match="外部进程"):
            controller._stop_owned_decider()
        controller._start_decider("start")
        assert launch.call_args.args[0] == [
            str(executable),
            str(script),
            "--checkpoint",
            str(checkpoint),
            "--port",
            "8895",
        ]
        assert launch.call_args.kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert controller._decorate(status(True)).actions == ["restart"]
        controller._start_decider("start")
        assert launch.call_count == 1
    finally:
        controller.close()


def test_unknown_occupied_port_is_never_killed_or_duplicated(tmp_path, monkeypatch):
    controller = control.DependencyController(tmp_path, DependencyMonitor(None, "http://127.0.0.1:8895"))
    monkeypatch.setattr(control, "probe_decider", lambda *args: status())
    monkeypatch.setattr(control.socket, "create_connection", Mock(return_value=MagicMock()))
    launch = Mock()
    monkeypatch.setattr(control.subprocess, "Popen", launch)
    try:
        with pytest.raises(control.DependencyControlError, match="端口已占用"):
            controller._start_decider("start")
        launch.assert_not_called()
        assert controller._decorate(status(True)).actions == []
        assert "外部" in controller._decorate(status(True)).control_note
    finally:
        controller.close()


def test_raw_task_errors_are_redacted_and_health_cache_invalidated(tmp_path, monkeypatch):
    monitor = DependencyMonitor(None)
    controller = control.DependencyController(tmp_path, monitor)
    monkeypatch.setattr(controller, "_mineru", Mock(side_effect=OSError("secret diagnostic")))
    operation = control.DependencyOperation(
        id="op", service="mineru", action="start", status="running", detail="start", started_at="now"
    )
    monitor._expires = 999999999999
    try:
        controller._run(operation)
        result = controller._decorate(
            DependencyStatus(id="mineru", title="MinerU", status="unknown", detail="unknown")
        )
        assert result.operation.status == "failed"
        assert "secret" not in result.model_dump_json()
        assert monitor._expires == 0
    finally:
        controller.close()


def test_owned_decider_stop_targets_only_its_process_tree(tmp_path, monkeypatch):
    controller = control.DependencyController(tmp_path, DependencyMonitor(None))
    process = Mock(pid=12345)
    process.poll.return_value = None
    controller._decider = process
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    kill = Mock()
    monkeypatch.setattr(control.subprocess, "run", run)
    monkeypatch.setattr(control.os, "killpg", kill, raising=False)
    try:
        controller._stop_owned_decider()
        if control.os.name == "nt":
            assert run.call_args.args[0] == ["taskkill", "/PID", "12345", "/T", "/F"]
        else:
            kill.assert_called_once_with(12345, control.signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=10)
        assert controller._decider is None
    finally:
        controller.close()


def test_health_wait_is_bounded_and_never_restarts_on_timeout(tmp_path, monkeypatch):
    controller = control.DependencyController(tmp_path, DependencyMonitor(None))
    clock = Mock(side_effect=[0, 0, 121])
    monkeypatch.setattr(control.time, "monotonic", clock)
    monkeypatch.setattr(control.time, "sleep", Mock())
    monkeypatch.setattr(control, "probe_mineru", Mock(return_value=status()))
    run = Mock()
    monkeypatch.setattr(control.subprocess, "run", run)
    try:
        with pytest.raises(control.DependencyControlError, match="不会自动反复重启"):
            controller._wait_ready("mineru")
        run.assert_not_called()
    finally:
        controller.close()


def test_permission_failure_cannot_be_bypassed_by_start_request(tmp_path, monkeypatch):
    controller = control.DependencyController(tmp_path, DependencyMonitor(Path("mineru")))
    denied = control.DependencyStatus(
        id="mineru", title="MinerU", status="permission_error", detail="permission"
    )
    monkeypatch.setattr(control, "probe_mineru", Mock(return_value=denied))
    run = Mock()
    monkeypatch.setattr(control.subprocess, "run", run)
    try:
        assert controller._decorate(denied).actions == []
        with pytest.raises(control.DependencyControlError, match="无写入权限"):
            controller._mineru("start")
        run.assert_not_called()
    finally:
        controller.close()


def test_decider_denied_port_fails_before_loading_model(tmp_path, monkeypatch):
    controller = control.DependencyController(tmp_path, DependencyMonitor(None, "http://127.0.0.1:18895"))
    monkeypatch.setattr(control, "probe_decider", lambda *args: status())
    monkeypatch.setattr(control.socket, "create_connection", Mock(side_effect=OSError("not listening")))
    listener = MagicMock()
    listener.__enter__.return_value.bind.side_effect = PermissionError("private system diagnostic")
    monkeypatch.setattr(control.socket, "socket", Mock(return_value=listener))
    launch = Mock()
    monkeypatch.setattr(control.subprocess, "Popen", launch)
    try:
        with pytest.raises(control.DependencyControlError, match="18895") as error:
            controller._start_decider("start")
        assert "private" not in str(error.value)
        launch.assert_not_called()
    finally:
        controller.close()
