"""Windows打包DSH的Sharp原生加载兼容；仅使用宿主显式配置的固定版本依赖。"""

import hashlib
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class DshNativeSettings(BaseModel):
    """无凭证的部署配置，模型不能设置路径或选择任意原生模块。"""

    model_config = ConfigDict(extra="forbid")
    windows_sharp_native: str


def configured_sharp_native(root: Path) -> Path | None:
    """启动时读取显式兼容配置；缺文件保留SDK原生行为，错误配置直接失败。"""
    config = root / "dsh-runtime.json"
    if os.name != "nt" or not config.is_file():
        return None
    settings = DshNativeSettings.model_validate_json(config.read_text("utf-8"))
    path = Path(settings.windows_sharp_native)
    if not path.is_absolute() or not path.is_file() or path.name != "sharp-win32-x64-0.35.5.node":
        raise ValueError("DSH Sharp原生依赖须为已安装的0.35.5绝对路径")
    for dependency in ("libvips-42.dll", "libvips-cpp-8.18.7.dll"):
        if not (path.parent / dependency).is_file():
            raise ValueError("DSH Sharp原生依赖缺少配套DLL")
    return path


def native_dependency_identity(path: Path) -> str:
    """记录准确二进制指纹以便诊断，不输出环境变量或凭证。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()
