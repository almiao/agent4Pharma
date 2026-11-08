"""资料模块的逐次下载文件账本；原子写入，不修改发现批次或来源覆盖声明。"""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest, PaperDownloadResult


@dataclass
class PaperDownloadRepository:
    """每次尝试独立文件，支持并发写入与追加失败重试历史。"""

    root: Path

    def save(self, result: PaperDownloadResult) -> None:
        """将完整结果原子落盘；不会部分覆盖成功结果。"""
        directory = self.root / "paper-downloads" / "attempts"
        directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(result.id.encode()).hexdigest()
        path = directory / (key + ".json")
        if path.exists():
            old = PaperDownloadResult.model_validate_json(path.read_text(encoding="utf-8"))
            if old != result:
                raise ValueError("同次下载尝试不可改写，请以新尝试追加结果")
            return
        temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)

    def results(self, batch_id: str = "") -> list[PaperDownloadResult]:
        """读取批次最新逐篇结果，空批次返回全部文献的最新结果。"""
        latest: dict[str, PaperDownloadResult] = {}
        for result in self.attempts(batch_id):
            key = result.request.decision_id
            if key not in latest or latest[key].created_at < result.created_at:
                latest[key] = result
        return sorted(latest.values(), key=lambda row: row.request.title)

    def attempts(self, batch_id: str = "") -> list[PaperDownloadResult]:
        """公开读取原始尝试历史，供恢复与审计使用，不合并重试记录。"""
        attempts = [
            PaperDownloadResult.model_validate_json(path.read_text(encoding="utf-8"))
            for path in (self.root / "paper-downloads" / "attempts").glob("*.json")
        ]
        return [row for row in attempts if not batch_id or row.request.batch_id == batch_id]

    def verified(self, request: PaperDownloadRequest) -> PaperDownloadResult | None:
        """只复用准确绑定、工作目录内且哈希仍一致的已校验主文，不接受调试记录替代。"""
        result = next(
            (
                r
                for r in self.results(request.batch_id)
                if r.request == request and r.status == "downloaded" and r.identity_match
            ),
            None,
        )
        if result is None:
            return None
        try:
            path = Path(result.file).resolve(strict=True)
            if not path.is_relative_to((self.root / "paper-downloads").resolve()):
                return None
            with path.open("rb") as stream:
                if stream.read(4) != b"%PDF":
                    return None
                stream.seek(0)
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            return None
        return result if digest == result.sha256 else None
