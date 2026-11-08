"""将全篇 PDF 提交到 MinerU 缓存队列，立即返回；不把全文交给模型。"""

import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, ValidationError

from agent4pharma.infrastructure.llm.mineru_runtime import mineru_environment
from agent4pharma.modules.materials.document_reading import DocumentPreparation, VerifiedDocument


class _Parse(BaseModel):
    sha256: str
    tier: str
    status: str


class _Response(BaseModel):
    class Error(BaseModel):
        code: str

    parse: _Parse | None = None
    error: Error | None = None


def prepare_document(
    executable: Path | None, document: VerifiedDocument, home: Path | None = None
) -> DocumentPreparation:
    """使用现有本地配置，按文件 hash 复用全篇解析；不更换引擎或远程上传。"""
    if executable is None:
        return DocumentPreparation(
            sha256=document.sha256, status="unavailable", error_code="mineru_not_installed"
        )
    try:
        result = subprocess.run(
            [
                str(executable),
                "parse",
                str(document.path),
                "--pages",
                "all",
                "--limit",
                "1",
                "--no-wait",
                "--json",
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            env=mineru_environment(home),
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    except subprocess.TimeoutExpired:
        return DocumentPreparation(sha256=document.sha256, status="pending", error_code="preparation_timeout")
    except OSError:
        return DocumentPreparation(
            sha256=document.sha256, status="unavailable", error_code="mineru_launch_failed"
        )
    try:
        response = _Response.model_validate_json(result.stdout)
    except ValidationError:
        return DocumentPreparation(sha256=document.sha256, status="failed", error_code="preparation_failed")
    parsed = response.parse
    if result.returncode or parsed is None:
        code = response.error.code if response.error else ""
        if code in {
            "quality_tier_unavailable",
            "engine_unavailable",
            "parse_server_unavailable",
            "server_busy",
        }:
            return DocumentPreparation(sha256=document.sha256, status="pending", error_code=code)
        if code == "server_not_running":
            return DocumentPreparation(sha256=document.sha256, status="unavailable", error_code=code)
        return DocumentPreparation(sha256=document.sha256, status="failed", error_code="preparation_failed")
    if parsed.sha256 != document.sha256 or parsed.tier != "standard":
        return DocumentPreparation(
            sha256=document.sha256, status="failed", error_code="preparation_identity_or_tier"
        )
    status = "ready" if parsed.status == "done" else "failed" if parsed.status == "failed" else "pending"
    return DocumentPreparation(sha256=document.sha256, status=status)
