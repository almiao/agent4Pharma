"""批次只读投影的进程内缓存；以SQLite实际提交版本失效，不以TTL掩盖新状态。"""

import json
import sqlite3
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from pydantic import JsonValue

from agent4pharma.modules.discovery.run_records import DiscoveryRunSnapshot


class DiscoveryQueryCache:
    """服务拥有只读版本观察连接；最多八个批次，返回独立对象，关闭时释放连接。"""

    def __init__(self, root: Path, read: Callable[[str], DiscoveryRunSnapshot]) -> None:
        self.path = (root / "discovery.sqlite3").resolve()
        self.read = read
        self.lock = Lock()
        self.connection: sqlite3.Connection | None = None
        self.file_identity: tuple[int, int] | None = None
        self.version = -1
        self.values: OrderedDict[str, str] = OrderedDict()

    def get(self, batch: str) -> dict[str, JsonValue]:
        """提交、数据库替换或冷读时重算；读取期间有提交则不缓存，错误不缓存。"""
        with self.lock:
            info = self.path.stat()
            identity = (info.st_dev, info.st_ino)
            if self.connection is None or identity != self.file_identity:
                self._close()
                self.connection = sqlite3.connect(
                    self.path.as_uri() + "?mode=ro", uri=True, check_same_thread=False
                )
                self.file_identity = identity
            version = self.connection.execute("PRAGMA data_version").fetchone()[0]
            if version != self.version:
                self.values.clear()
                self.version = version
            value = self.values.get(batch)
            if value is None:
                value = self.read(batch).model_dump_json()
                if self.connection.execute("PRAGMA data_version").fetchone()[0] == version:
                    self.values[batch] = value
                    if len(self.values) > 8:
                        self.values.popitem(last=False)
            else:
                self.values.move_to_end(batch)
        return json.loads(value)

    def _close(self) -> None:
        if self.connection is not None:
            self.connection.close()
        self.connection = None
        self.version = -1
        self.values.clear()

    def close(self) -> None:
        """释放本服务拥有的连接及投影，不改数据库或业务任务。"""
        with self.lock:
            self._close()
