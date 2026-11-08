"""日常跑批的原子文件状态；批次资料与模型产物仍由原仓储拥有。"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agent4pharma.modules.discovery.daily_batch_runs import DailyBatchRun


@dataclass
class DailyBatchRunRepository:
    """由调用方短锁协调读改写，不保存凭证或任意执行命令。"""

    root: Path

    def all(self) -> list[DailyBatchRun]:
        """只读取完整JSON记录，忽略未完成的临时文件。"""
        runs = [
            DailyBatchRun.model_validate_json(path.read_text(encoding="utf-8"))
            for path in (self.root / "daily-batch-runs").glob("batch-run-*.json")
        ]
        return sorted(runs, key=lambda run: run.created_at, reverse=True)

    def save(self, run: DailyBatchRun) -> None:
        """原子替换准确身份记录；中断写入不留下半份JSON。"""
        if not re.fullmatch(r"batch-run-[a-f0-9]{32}", run.id):
            raise ValueError("跑批身份无效")
        directory = self.root / "daily-batch-runs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (run.id + ".json")
        temporary = path.with_suffix("." + uuid4().hex + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(run.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
