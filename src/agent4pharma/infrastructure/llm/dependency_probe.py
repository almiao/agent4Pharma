"""只读检查 MinerU；限制等待、过滤诊断，并复用短期检查结果。"""

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from agent4pharma.infrastructure.llm.mineru_runtime import mineru_environment
from agent4pharma.runtime.dependency_status import DependencySnapshot, DependencyStatus, ParserTasks


class _LocalParser(BaseModel):
    healthy: bool = False
    starting: bool = False


class _Parsers(BaseModel):
    local: _LocalParser = _LocalParser()


class _MineruStatus(BaseModel):
    running: bool
    parse_queue_length: int = 0
    parse_server: _Parsers | None = None


def probe_mineru(executable: Path | None, home: Path | None = None) -> DependencyStatus:
    """仅查询已运行服务，不启动服务、不解析文件、不输出 CLI 原始错误。"""
    result = DependencyStatus(id="mineru", title="MinerU · PDF 解析", status="unknown", detail="检查失败")
    if executable is None:
        return result.model_copy(update={"status": "not_installed", "detail": "未安装 MinerU"})
    try:
        process = subprocess.run(
            [str(executable), "server", "status", "--json"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=6,
            env=mineru_environment(home),
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        if process.returncode:
            try:
                error = json.loads(process.stdout).get("error", {})
                message = str(error.get("message", ""))
                code = error.get("code", "")
            except (ValueError, AttributeError):
                message, code = "", ""
            if "WinError 5" in message or "Permission denied" in message or "拒绝访问" in message:
                return result.model_copy(
                    update={
                        "status": "permission_error",
                        "detail": "无权访问本机资料库；请检查服务工作目录的写入权限",
                    }
                )
            if code == "server_not_running":
                return result.model_copy(update={"status": "stopped", "detail": "服务未启动"})
            return result.model_copy(
                update={"status": "error", "detail": "官方 CLI 状态查询失败，请查看本机诊断"}
            )
        status = _MineruStatus.model_validate_json(process.stdout)
    except subprocess.TimeoutExpired:
        return result.model_copy(update={"detail": "服务查询超时（6秒），状态待确认"})
    except (OSError, ValidationError):
        return result.model_copy(update={"status": "error", "detail": "服务查询失败或响应格式无效"})
    if not status.running:
        return result.model_copy(update={"status": "stopped", "detail": "服务未启动"})
    local = status.parse_server.local if status.parse_server is not None else _LocalParser()
    return result.model_copy(
        update={
            "status": "healthy" if local.healthy else "starting" if local.starting else "unavailable",
            "detail": "本地解析引擎健康"
            if local.healthy
            else "本地解析引擎启动中"
            if local.starting
            else "本地解析引擎不可用",
            "queued": status.parse_queue_length,
        }
    )


def probe_decider(endpoint: str) -> DependencyStatus:
    """只向已配置的回环地址请求健康接口，不触发推理。"""
    result = DependencyStatus(id="decider", title="本地 Decider", status="disabled", detail="未启用本地预选")
    if not endpoint:
        return result
    result = result.model_copy(update={"status": "unknown"})
    parts = urlsplit(endpoint)
    if (
        parts.scheme != "http"
        or parts.hostname not in {"localhost", "127.0.0.1"}
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        return result.model_copy(update={"status": "error", "detail": "健康检查地址不符合回环约束"})
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(endpoint.rstrip("/") + "/health")
            response.raise_for_status()
            identity = response.json()
            if (
                not isinstance(identity, dict)
                or identity.get("model") != "perplexity-ai/pplx-decider-flash-v1.2"
            ):
                raise ValueError("unexpected local service identity")
    except httpx.ConnectError:
        return result.model_copy(update={"status": "stopped", "detail": "本地服务未监听配置端口，可显式启动"})
    except httpx.TimeoutException:
        return result.model_copy(update={"detail": "健康检查超时，暂不能确认；请刷新重查"})
    except (httpx.HTTPError, ValueError):
        return result.model_copy(
            update={"status": "error", "detail": "健康响应失败或服务身份不匹配，请检查本机服务"}
        )
    return result.model_copy(update={"status": "healthy", "detail": "本地健康接口可响应"})


class DependencyMonitor:
    """进程内串行健康检查，缓存20秒；读取不触发模型调用或服务恢复。"""

    def __init__(self, executable: Path | None, decider_endpoint: str = "", mineru_home: Path | None = None):
        self.executable = executable
        self.mineru_home = mineru_home
        self.decider_endpoint = decider_endpoint
        self._lock = Lock()
        self._expires = 0.0
        self._snapshot: DependencySnapshot | None = None

    def parser_tasks(self) -> ParserTasks:
        """按需读取最近20条解析任务，失败明确报错，不返回伪造空集合。"""
        if self.executable is None:
            raise ValueError("未安装 MinerU，无法读取解析任务")
        try:
            response = subprocess.run(
                [str(self.executable), "list", "parses", "--limit", "20", "--json"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=6,
                env=mineru_environment(self.mineru_home),
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
            if response.returncode:
                raise ValueError("MinerU 解析任务查询失败")
            return ParserTasks.model_validate_json(response.stdout).model_copy(
                update={"checked_at": datetime.now(UTC).isoformat()}
            )
        except (OSError, subprocess.TimeoutExpired, ValidationError):
            raise ValueError("MinerU 解析任务读取失败或超时，请稍后重试") from None

    def invalidate(self) -> None:
        """服务显式操作后使短期缓存过期；不启动服务或修改配置。"""
        with self._lock:
            self._expires = 0.0

    def snapshot(self, model_configured: bool) -> DependencySnapshot:
        """返回已脱敏状态，外部模型只检查配置，不用付费请求作健康探测。"""
        with self._lock:
            if self._snapshot is None or time.monotonic() >= self._expires:
                self._snapshot = DependencySnapshot(
                    checked_at=datetime.now(UTC).isoformat(),
                    items=[
                        DependencyStatus(
                            id="backend", title="运行台接口", status="healthy", detail="接口可响应"
                        ),
                        probe_mineru(self.executable, self.mineru_home),
                        probe_decider(self.decider_endpoint),
                    ],
                )
                self._expires = time.monotonic() + 20
            return self._snapshot.model_copy(
                update={
                    "items": [
                        *self._snapshot.items,
                        DependencyStatus(
                            id="model",
                            title="采编模型",
                            status="configured" if model_configured else "unavailable",
                            detail="凭证已配置，未发起模型健康请求" if model_configured else "未配置模型凭证",
                        ),
                    ]
                }
            )
