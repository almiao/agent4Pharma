"""下载任务的原子文件状态；与不可改写的逐次下载账本分开保存。"""

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.modules.materials.paper_download_jobs import PaperDownloadJob


@dataclass
class PaperDownloadJobRepository:
    """在调用方的短任务锁内读改写，断电不会留下半份 JSON。"""

    root: Path

    def _path(self, job_id: str) -> Path:
        if not re.fullmatch(r"pdf-[a-f0-9]{32}", job_id):
            raise ValueError("下载任务 ID 无效")
        return self.root / "paper-downloads/jobs" / (job_id + ".json")

    def save(self, job: PaperDownloadJob) -> None:
        """原子替换本任务快照，不修改逐次下载结果。"""
        path = self._path(job.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(job.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def get(self, job_id: str) -> PaperDownloadJob:
        """读取指定任务，拒绝路径穿越。"""
        path = self._path(job_id)
        if not path.is_file():
            raise KeyError("下载任务不存在")
        return PaperDownloadJob.model_validate_json(path.read_text(encoding="utf-8"))

    def all(self) -> list[PaperDownloadJob]:
        """返回已持久化任务，不返回临时写入文件。"""
        jobs = [
            PaperDownloadJob.model_validate_json(p.read_text(encoding="utf-8"))
            for p in (self.root / "paper-downloads/jobs").glob("pdf-*.json")
        ]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)
