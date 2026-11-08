"""在独立本地 CLI 环境调用 MinerU；不启动模型 Agent、不上传或自动降级。"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError

from agent4pharma.infrastructure.llm.mineru_identity import resolve_mineru_identity
from agent4pharma.infrastructure.llm.mineru_runtime import mineru_environment
from agent4pharma.modules.materials.document_reading import DocumentContent, DocumentReadResult


class _MineruError(BaseModel):
    code: str
    retryable: bool = False


class _MineruResponse(BaseModel):
    content: DocumentContent | None = None
    error: _MineruError | None = None


def find_mineru() -> Path | None:
    """查找显式安装或当前用户的隔离环境；不安装依赖、不执行 PATH 以外脚本。"""
    command = shutil.which("mineru")
    if command:
        return Path(command)
    command = Path.home() / ".codex" / "tools" / "mineru" / "Scripts" / "mineru.exe"
    return command if command.is_file() else None


@dataclass
class MineruDocumentReader:
    """每次调用有硬时限，默认质量由本地配置选择；不存在到其他解析器的回退。"""

    executable: Path | None
    timeout_seconds: int = 90
    cache_only: bool = False
    home: Path | None = None

    def parse(
        self, path: Path, sha256: str, start_page: int, end_page: int, after: str = ""
    ) -> DocumentReadResult:
        """读取最多十页并校验结果哈希；只有结构化错误代码可离开外部边界。"""
        if not 1 <= start_page <= end_page or end_page - start_page >= 10:
            raise ValueError("每次解析需指定连续1至10页")
        if len(after) > 2048 or any(ord(character) < 32 for character in after):
            raise ValueError("续读游标无效")
        if self.executable is None:
            return DocumentReadResult(status="unavailable", error_code="mineru_not_installed")
        identity = resolve_mineru_identity(self.executable, sha256, self.home)
        if isinstance(identity, DocumentReadResult):
            if self.cache_only:
                return identity
            # 历史首次解析可尚未登记文档；原有 parse 路径仍负责创建缓存。
            locator_id = sha256
        else:
            locator_id = identity.short_id
        continuation = ["--after", after] if after else []
        # 论文首页的作者单位常在页尾，保留单独读取首页时的元数据空间。
        read_limit = 6000 if self.cache_only and (start_page, end_page) != (1, 1) else 12000
        cached = self._invoke(
            [
                "read",
                f"doc:{locator_id}/tier:standard/page:{start_page}-{end_page}",
                "--limit",
                str(read_limit),
                "--json",
                *continuation,
            ],
            sha256,
            min(10, self.timeout_seconds),
            read_limit,
        )
        if cached.status == "available" and cached.content.tier != "flash":
            return cached
        if cached.content is None and cached.error_code not in {
            "not_cached",
            "page_not_cached",
            "cache_miss",
            "doc_not_found",
        }:
            return cached
        if self.cache_only:
            return DocumentReadResult(status="pending", error_code="document_not_prepared", retryable=True)
        parsed = self._invoke(
            [
                "parse",
                str(path.resolve()),
                "--pages",
                f"{start_page}-{end_page}",
                "--limit",
                str(read_limit),
                "--wait",
                "60",
                "--json",
                *continuation,
            ],
            sha256,
            max(1, self.timeout_seconds - 10),
            read_limit,
        )
        if parsed.status == "available" and parsed.content.tier == "flash":
            return DocumentReadResult(status="unavailable", error_code="quality_tier_unavailable")
        return parsed

    def _invoke(self, arguments: list[str], sha256: str, timeout: int, read_limit: int) -> DocumentReadResult:
        environment = mineru_environment(self.home)
        try:
            result = subprocess.run(
                [str(self.executable), *arguments],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=environment,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
        except subprocess.TimeoutExpired:
            return DocumentReadResult(status="pending", error_code="parse_timeout", retryable=True)
        except OSError:
            return DocumentReadResult(status="unavailable", error_code="mineru_launch_failed")
        try:
            payload = TypeAdapter(_MineruResponse | DocumentContent).validate_json(result.stdout)
            if isinstance(payload, DocumentContent):
                payload = _MineruResponse(content=payload)
        except ValidationError:
            return DocumentReadResult(status="failed", error_code="invalid_mineru_response")
        if payload.error:
            if payload.error.code in {"parse_wait_timeout", "parse_timeout"}:
                return DocumentReadResult(status="pending", error_code=payload.error.code, retryable=True)
            if payload.error.code in {"quality_tier_unavailable", "no_engine", "server_not_running"}:
                return DocumentReadResult(status="unavailable", error_code=payload.error.code)
            return DocumentReadResult(
                status="failed", error_code=payload.error.code, retryable=payload.error.retryable
            )
        if result.returncode:
            return DocumentReadResult(status="failed", error_code="mineru_process_failed")
        if payload.content is None:
            return DocumentReadResult(status="pending", error_code="parse_pending", retryable=True)
        if payload.content.sha256 != sha256:
            return DocumentReadResult(status="failed", error_code="document_hash_mismatch")
        if len(payload.content.content) > read_limit:
            return DocumentReadResult(status="unavailable", error_code="document_block_exceeds_read_limit")
        locators = re.findall(r"doc:[A-Za-z0-9]+(?:/[A-Za-z0-9:_-]+)*", payload.content.content)
        for content_range in payload.content.content_ranges:
            locators.extend([content_range.start, content_range.end])
        return DocumentReadResult(
            status="available", content=payload.content, locators=list(dict.fromkeys(locators))
        )
