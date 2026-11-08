"""本机依赖的有界显式控制；不安装、下载、推理或接管未知进程。"""

import os
import signal
import socket
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from agent4pharma.infrastructure.llm.dependency_probe import DependencyMonitor, probe_decider, probe_mineru
from agent4pharma.infrastructure.llm.mineru_runtime import mineru_environment
from agent4pharma.runtime.dependency_status import (
    DependencyControlRequest,
    DependencyOperation,
    DependencySnapshot,
    DependencyStatus,
)
from agent4pharma.shared.clock import utcnow


class DependencyControlError(ValueError):
    """可向界面公开的本机服务控制错误，不含供应商诊断。"""


class DependencyController:
    """只持有自己启动的 Decider 进程；MinerU 生命周期交官方 CLI 管理。"""

    def __init__(self, root: Path, monitor: DependencyMonitor) -> None:
        self.root, self.monitor = root.resolve(), monitor
        self._lock = Lock()
        self._operations: dict[str, DependencyOperation] = {}
        self._decider: subprocess.Popen | None = None
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dependencies")

    def control(self, request: DependencyControlRequest) -> DependencyOperation:
        """同一服务正在启动时复用操作；请求只排队，不等待加载模型或解析引擎。"""
        with self._lock:
            current = self._operations.get(request.service)
            if current and current.status == "running":
                return current
            operation = DependencyOperation(
                id=uuid.uuid4().hex,
                service=request.service,
                action=request.action,
                status="running",
                detail="正在启动并确认健康" if request.action == "start" else "正在重启并确认健康",
                started_at=utcnow(),
            )
            self._operations[request.service] = operation
            self._executor.submit(self._run, operation)
            return operation

    def snapshot(self, configured: bool) -> DependencySnapshot:
        """健康读投影加上控制能力；读取本身绝不启动服务。"""
        snapshot = self.monitor.snapshot(configured)
        with self._lock:
            return snapshot.model_copy(update={"items": [self._decorate(item) for item in snapshot.items]})

    def close(self) -> None:
        """停止接受控制操作；依赖服务独立存在，不随关闭后台中断已有解析/推理。"""
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _decider_paths(self) -> tuple[Path, Path, Path]:
        executable = (
            self.root / "runtimes" / "decider" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        return (
            executable,
            self.root.parent / "scripts" / "serve_decider.py",
            self.root / "models/decider-flash-v1.2",
        )

    def _decider_port(self) -> int:
        parts = urlsplit(self.monitor.decider_endpoint)
        if (
            parts.scheme != "http"
            or parts.hostname not in {"localhost", "127.0.0.1"}
            or parts.username
            or parts.password
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or not parts.port
        ):
            raise DependencyControlError("仅支持已配置的本机回环 Decider 服务")
        return parts.port

    def _decorate(self, item: DependencyStatus) -> DependencyStatus:
        actions, note = [], ""
        if item.id == "mineru" and self.monitor.executable:
            if item.status == "healthy":
                actions = ["restart"]
            elif item.status == "stopped":
                actions = ["start"]
            elif item.status == "unavailable":
                actions = ["restart"]
            elif item.status == "permission_error":
                note = "先修复资料库访问权限，再刷新检查；运行台不会删除锁或修改权限"
        elif item.id == "decider":
            executable, script, checkpoint = self._decider_paths()
            try:
                self._decider_port()
            except ValueError:
                note = "本机服务未启用或地址不支持本机控制"
            else:
                owned = self._decider is not None and self._decider.poll() is None
                if owned:
                    actions = ["restart"]
                elif item.status == "healthy":
                    note = "服务由外部启动，本运行台不接管其重启"
                elif (
                    executable.is_file() and script.is_file() and (checkpoint / "installation.json").is_file()
                ):
                    actions = ["start"]
                else:
                    note = "本机 Decider 环境、启动脚本或权重未安装完整"
        operation = self._operations.get(item.id)
        if operation and operation.status == "running":
            actions = []
        return item.model_copy(
            update={
                "actions": actions,
                "control_note": note,
                "operation": operation,
                "work_directory": str(self.monitor.mineru_home)
                if item.id == "mineru" and self.monitor.mineru_home
                else "",
            }
        )

    def _run(self, operation: DependencyOperation) -> None:
        try:
            if operation.service == "mineru":
                self._mineru(operation.action)
            else:
                self._start_decider(operation.action)
            result = operation.model_copy(update={"status": "succeeded", "detail": "服务健康检查已通过"})
        except Exception as exc:
            # 后台任务边界只公开本适配器定义的错误，供应商日志和环境不进入响应。
            message = (
                str(exc) if isinstance(exc, DependencyControlError) else "服务操作失败：" + type(exc).__name__
            )
            result = operation.model_copy(update={"status": "failed", "detail": message})
        finally:
            self.monitor.invalidate()
        with self._lock:
            self._operations[operation.service] = result

    def _mineru(self, action: str) -> None:
        executable = self.monitor.executable
        if not executable:
            raise DependencyControlError("未安装 MinerU；本操作不会自动安装")
        status = probe_mineru(executable, self.monitor.mineru_home)
        if status.status == "permission_error":
            raise DependencyControlError("MinerU 资料库无写入权限；未启动服务或修改锁")
        if action == "start" and status.status == "healthy":
            return
        result = subprocess.run(
            [str(executable), "server", action],
            capture_output=True,
            timeout=90,
            env=mineru_environment(self.monitor.mineru_home),
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        if result.returncode:
            raise DependencyControlError("MinerU 服务命令失败，请检查本机服务权限或锁占用；未更改锁或配置")
        self._wait_ready("mineru")

    def _start_decider(self, action: str) -> None:
        port = self._decider_port()
        if action == "restart":
            self._stop_owned_decider()
        elif probe_decider(self.monitor.decider_endpoint).status == "healthy":
            return
        if self._decider is None or self._decider.poll() is not None:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    raise DependencyControlError(
                        "Decider 端口已占用且未确认健康；未启动第二个进程或终止未知进程"
                    )
            except OSError:
                pass
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise DependencyControlError(
                    f"Decider 无法绑定本机端口 {port}，请检查端口占用或系统访问限制；未加载模型"
                ) from exc
            executable, script, checkpoint = self._decider_paths()
            if (
                not executable.is_file()
                or not script.is_file()
                or not (checkpoint / "installation.json").is_file()
            ):
                raise DependencyControlError("本机 Decider 环境、启动脚本或权重未安装完整；不会自动下载")
            self._decider = subprocess.Popen(
                [str(executable), str(script), "--checkpoint", str(checkpoint), "--port", str(port)],
                cwd=self.root.parent,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                **(
                    {"creationflags": subprocess.CREATE_NO_WINDOW}
                    if os.name == "nt"
                    else {"start_new_session": True}
                ),
            )
        self._wait_ready("decider")

    def _stop_owned_decider(self) -> None:
        process = self._decider
        if process is None or process.poll() is not None:
            raise DependencyControlError("无法确认本运行台启动的 Decider 进程；拒绝终止外部进程")
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode:
                raise DependencyControlError("Decider 未能停止，未启动第二个进程")
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
        self._decider = None

    def _wait_ready(self, service: str) -> None:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if service == "decider" and self._decider is not None and self._decider.poll() is not None:
                raise DependencyControlError("Decider 启动进程已退出，服务未就绪")
            status = (
                probe_mineru(self.monitor.executable, self.monitor.mineru_home)
                if service == "mineru"
                else probe_decider(self.monitor.decider_endpoint)
            )
            if status.status == "healthy":
                return
            time.sleep(2)
        raise DependencyControlError("服务仍未通过健康检查；不会自动反复重启，请刷新后查看")
