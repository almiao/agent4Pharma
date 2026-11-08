"""只发现 PDF 收件位置：系统下载目录和 Chrome 下载偏好，不读取历史、账号或凭证。"""

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DownloadLocations:
    """去重后的宿主目录和发现失败说明，不包含浏览器其他设置。"""

    directories: tuple[Path, ...]
    warnings: tuple[str, ...] = ()


def system_download_directory(home: Path) -> Path:
    """读取 Windows 已重定向的下载位置；未登记时使用用户 Downloads。"""
    if os.name != "nt":
        return home / "Downloads"
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
    except FileNotFoundError:
        return home / "Downloads"
    if not isinstance(value, str) or not value.strip():
        raise ValueError("系统下载目录配置无效")
    return Path(os.path.expandvars(value))


def discover_download_locations(
    *, home: Path | None = None, chrome_root: Path | None = None, system_directory: Path | None = None
) -> DownloadLocations:
    """只检查 Chrome Default/Profile 配置中的下载路径，不启动或连接浏览器。

    坏配置和权限问题作为提示返回，其余位置继续可用；测试显式注入宿主路径。
    """
    home = home or Path.home()
    directories = [home / "Downloads"]
    warnings = []
    try:
        directories.insert(0, system_directory or system_download_directory(home))
    except (OSError, ValueError):
        warnings.append("系统下载目录读取失败，仍检查常规 Downloads 目录")
    chrome_root = (
        chrome_root
        or Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "Google/Chrome/User Data"
    )
    try:
        profiles = [
            path
            for path in chrome_root.iterdir()
            if path.name == "Default" or path.name.startswith("Profile ")
        ]
    except FileNotFoundError:
        profiles = []
    except OSError:
        profiles = []
        warnings.append("Chrome 配置目录无法读取，未确认其下载位置")
    for profile in profiles:
        try:
            preferences = profile / "Preferences"
            if preferences.stat().st_size > 16_000_000:
                raise ValueError("下载偏好文件超出读取上限")
            data = json.loads(preferences.read_text(encoding="utf-8"))
            for section in ("download", "savefile"):
                settings = data.get(section, {})
                value = settings.get("default_directory") if isinstance(settings, dict) else None
                if isinstance(value, str) and value.strip():
                    path = Path(os.path.expandvars(value)).expanduser()
                    if path.is_absolute():
                        directories.append(path)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, AttributeError):
            warnings.append(f"Chrome {profile.name} 下载偏好读取失败，其自定义目录未确认")
    return DownloadLocations(tuple(dict.fromkeys(path.resolve() for path in directories)), tuple(warnings))
