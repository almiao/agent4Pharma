"""每次工作台操作独立连接，避免 HTTP/后台线程共享 SQLite 连接。"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def transaction(root: Path, connection: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """提交一个短工作单元，失败回滚，结束后始终关闭连接。"""
    if connection is not None:
        yield connection
        return
    db = sqlite3.connect(root / "discovery.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA temp_store=MEMORY")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
