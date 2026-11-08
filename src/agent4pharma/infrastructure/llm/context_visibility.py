"""执行器与MCP共享的活动窗口代次；缺失标记时保守失效，不推断资料仍可见。"""

from pathlib import Path


def read_visibility_epoch(path: Path) -> str:
    """仅读取执行器产生的小型代次文件；文件消失后也清空原有覆盖。"""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing"
