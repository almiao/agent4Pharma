"""记录可核对的SDK、执行文件与本地适配器身份，不将dev版本猜成上游commit。"""

import hashlib
from importlib.metadata import version
from pathlib import Path

from pydantic import BaseModel


class RuntimeBuildIdentity(BaseModel):
    """本地实际安装产物指纹；上游提交无法证明时明确未知。"""

    sdk_version: str
    executable_sha256: str
    window_adapter_sha256: str
    tool_guard_sha256: str
    upstream_commit: str | None = None


def runtime_build_identity() -> RuntimeBuildIdentity:
    """仅在工作进程启动时读取已安装执行文件；不下载运行时或访问网络。"""
    from deepseek_harness_runtime import bundled_runtime_path

    def digest(path: Path) -> str:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    return RuntimeBuildIdentity(
        sdk_version=version("deepseek-harness-sdk"),
        executable_sha256=digest(bundled_runtime_path()),
        window_adapter_sha256=digest(Path(__file__).with_name("dsh_revision_window.cjs")),
        tool_guard_sha256=digest(Path(__file__).with_name("dsh_tool_readiness.cjs")),
    )
