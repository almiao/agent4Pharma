"""用完整内容哈希查真实 MinerU 短身份，不猜固定长度或把哈希当 locator。"""

import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from agent4pharma.infrastructure.llm.mineru_runtime import mineru_environment
from agent4pharma.modules.materials.document_reading import DocumentReadResult


class MineruIdentity(BaseModel):
    """CLI 确认的内容身份；短 ID 可能因前缀冲突增长。"""

    sha256: str
    short_id: str = Field(pattern=r"^[a-zA-Z0-9]+$")


class _LookupFailure(BaseModel):
    class Error(BaseModel):
        code: str

    error: Error


def resolve_mineru_identity(
    executable: Path, sha256: str, home: Path | None = None
) -> MineruIdentity | DocumentReadResult:
    """只查询缓存元数据；错误保持技术状态，不启动解析或返回本地文件清单。"""
    try:
        result = subprocess.run(
            [str(executable), "show", "doc", sha256, "--json"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env=mineru_environment(home),
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    except subprocess.TimeoutExpired:
        return DocumentReadResult(status="pending", error_code="document_lookup_timeout", retryable=True)
    except OSError:
        return DocumentReadResult(status="unavailable", error_code="mineru_launch_failed")
    if result.returncode:
        try:
            code = _LookupFailure.model_validate_json(result.stdout).error.code
        except ValidationError:
            code = "document_lookup_failed"
        if code in {"server_not_running", "mineru_not_installed"}:
            return DocumentReadResult(status="unavailable", error_code=code)
        return DocumentReadResult(status="failed", error_code="document_lookup_failed")
    try:
        identity = MineruIdentity.model_validate_json(result.stdout)
    except ValidationError:
        return DocumentReadResult(status="failed", error_code="invalid_document_identity")
    if identity.sha256 != sha256:
        return DocumentReadResult(status="failed", error_code="document_hash_mismatch")
    return identity
