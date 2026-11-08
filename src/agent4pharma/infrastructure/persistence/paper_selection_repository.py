"""摘要筛选运行的追加文件版本；不覆盖历史、不开数据库事务执行模型。"""

import gzip
import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agent4pharma.modules.discovery.paper_selection import PaperSelectionRun
from agent4pharma.modules.discovery.paper_selection_queries import PaperSelectionSummary


@lru_cache(maxsize=128)
def _summary(path: Path, modified_ns: int, size: int) -> PaperSelectionSummary:
    """按不可变修订文件缓存目录字段；文件元数据变化即失效，不缓存可变领域对象。"""
    payload = json.loads(gzip.decompress(path.read_bytes()))
    run = payload["run"] if payload.get("format") == "paper-selection-v2" else payload
    return PaperSelectionSummary(
        id=run["id"],
        batch_id=run["batch_id"],
        created_at=run["created_at"],
        phase=run.get("phase", "abstracts"),
    )


def _encode(run: PaperSelectionRun) -> bytes:
    """同一准则、阶段指令与历史只存一次；精确内容寻址，不删除任务输入。"""
    payload = run.model_dump(mode="json")
    shared = {}
    histories: dict[tuple[str, ...], str] = {}
    values: dict[str, str] = {}
    for item in payload["items"]:
        for field in ("history", "policy", "stage_instructions"):
            if field not in item["request"]:
                continue
            value = item["request"][field]
            if field == "history":
                key = tuple(value)
                identity = histories.get(key)
            else:
                key = json.dumps(value, ensure_ascii=False, sort_keys=True)
                identity = values.get(key)
            if identity is None:
                canonical = json.dumps(value, ensure_ascii=False, sort_keys=True)
                identity = hashlib.sha256(canonical.encode()).hexdigest()
                shared[identity] = value
                if field == "history":
                    histories[key] = identity
                else:
                    values[key] = identity
            item["request"][field] = {"ref": identity}
    return gzip.compress(
        json.dumps(
            {"format": "paper-selection-v2", "shared": shared, "run": payload},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    )


def _decode(content: bytes) -> PaperSelectionRun:
    payload = json.loads(gzip.decompress(content))
    if payload.get("format") != "paper-selection-v2":
        return PaperSelectionRun.model_validate(payload)
    run = payload["run"]
    for item in run["items"]:
        for field in ("history", "policy", "stage_instructions"):
            if field not in item["request"]:
                continue
            item["request"][field] = payload["shared"][item["request"][field]["ref"]]
    return PaperSelectionRun.model_validate(run)


@dataclass
class PaperSelectionRepository:
    """单工作进程锁内使用；每个运行版本独立落盘，失败不覆盖上一版。"""

    root: Path

    def _directory(self, run_id: str) -> Path:
        if not re.fullmatch(r"selection-[a-f0-9]{24}", run_id):
            raise ValueError("无效摘要筛选运行 ID")
        return self.root / "paper-selections" / run_id

    def get(self, run_id: str) -> PaperSelectionRun:
        """读取该运行最新完整版本；不存在时抛 KeyError。"""
        paths = sorted(self._directory(run_id).glob("*.json.gz"))
        if not paths:
            raise KeyError("摘要筛选运行不存在")
        return _decode(paths[-1].read_bytes())

    def latest(self, batch_id: str = "") -> list[PaperSelectionRun]:
        """返回每个运行最新版本，按批次精确筛选，不改变运行状态。"""
        return [self.get(item.id) for item in self.summaries(batch_id)]

    def summaries(self, batch_id: str = "") -> list[PaperSelectionSummary]:
        """只读最新修订的目录信息，按准确批次筛选，不实例化历史论文池。"""
        summaries = []
        for directory in (self.root / "paper-selections").glob("selection-*"):
            paths = sorted(directory.glob("*.json.gz"))
            if not paths:
                continue
            path = paths[-1]
            info = path.stat()
            item = _summary(path, info.st_mtime_ns, info.st_size)
            if not batch_id or item.batch_id == batch_id:
                summaries.append(item)
        return sorted(summaries, key=lambda item: item.created_at)

    def save(self, run: PaperSelectionRun) -> None:
        """追加一版；调用方持 intelligence 工作锁，原子落盘后才推进内存 revision。"""
        import os
        import uuid

        directory = self._directory(run.id)
        directory.mkdir(parents=True, exist_ok=True)
        existing = sorted(directory.glob("*.json.gz"))
        revision = int(existing[-1].name.split(".")[0]) if existing else 0
        if run.revision != revision:
            raise ValueError("摘要筛选运行版本已变化，请重新读取")
        if existing:
            with gzip.open(existing[-1], "rt", encoding="utf-8") as stream:
                normalized = stream.read(40).startswith('{"format":"paper-selection-v2"')
            if normalized and self.get(run.id) == run:
                return
        version = run.model_copy(update={"revision": revision + 1})
        temporary = directory / (uuid.uuid4().hex + ".tmp")
        with temporary.open("xb") as stream:
            stream.write(_encode(version))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(directory / f"{version.revision:08d}.json.gz")
        run.revision = version.revision
