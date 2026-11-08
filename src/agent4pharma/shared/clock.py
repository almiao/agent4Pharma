"""UTC timestamps for persisted state."""

from datetime import datetime, timezone


def utcnow() -> str:
    """返回当前 UTC 时间的 ISO 字符串，持久化时保留时区。"""
    return datetime.now(timezone.utc).isoformat()
