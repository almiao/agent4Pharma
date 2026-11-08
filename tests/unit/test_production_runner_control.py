"""进程退出与撤销租约的离线验证；不启动模型或实际杀进程。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent4pharma.infrastructure.llm import production_runner as module


@pytest.mark.parametrize("cancelled", [True, False])
def test_runner_reaps_cancelled_process_but_allows_committed_result_to_finish(
    tmp_path, monkeypatch, cancelled
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-placeholder")
    process = MagicMock(pid=123)
    process.poll.return_value = None if cancelled else 0
    process.wait.return_value = 0
    popen = MagicMock(return_value=process)
    kill_tree = MagicMock()
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(module.subprocess, "run", kill_tree)
    runner = module.DshProductionRunner(tmp_path, lambda _: not cancelled)
    task = SimpleNamespace(id="bound-task", lease_token="lease", limits=SimpleNamespace(timeout_seconds=10))
    runner.run(task)
    if cancelled:
        if module.os.name == "nt":
            assert kill_tree.call_args.args[0] == ["taskkill", "/PID", "123", "/T", "/F"]
        else:
            process.kill.assert_called_once()
        process.wait.assert_called_once_with()
    else:
        kill_tree.assert_not_called()
        process.kill.assert_not_called()
        process.wait.assert_called_once_with(timeout=pytest.approx(0.5))


def test_runner_keeps_hard_timeout_and_reaps_process(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-placeholder")
    process = MagicMock(pid=123)
    process.poll.return_value = None
    monkeypatch.setattr(module.subprocess, "Popen", MagicMock(return_value=process))
    monkeypatch.setattr(module.subprocess, "run", MagicMock())
    clock = iter([0, 11])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    task = SimpleNamespace(id="bound-task", lease_token="lease", limits=SimpleNamespace(timeout_seconds=10))
    with pytest.raises(RuntimeError, match="硬超时"):
        module.DshProductionRunner(tmp_path, lambda _: True).run(task)
    process.wait.assert_called_once_with()
