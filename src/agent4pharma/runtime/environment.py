"""启动边界按用途加载白名单环境变量；不执行 .env 内容，不输出凭证。"""

import os
from pathlib import Path


def load_model_environment(path: Path) -> bool:
    """仅加载三个模型变量，现有进程环境优先；返回是否具备凭证，不返回值。

    支持 KEY=value 和成对单/双引号，不做变量展开、转义或执行脚本。
    不读取其他密钥；文件不存在时保留现行环境。调用方在启动配置之前执行。
    """
    _load_allowed_environment(path, {"DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"})
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


def load_wechat_environment(path: Path) -> None:
    """仅运行台启动读取公众号配置；进程环境优先，不返回凭证。"""
    _load_allowed_environment(
        path, {"PHARMA_WECHAT_APP_ID", "PHARMA_WECHAT_APP_SECRET", "PHARMA_WECHAT_ACCOUNT_NAME"}
    )


def _load_allowed_environment(path: Path, allowed: set[str]) -> None:
    if path.is_file():
        if path.stat().st_size > 100000:
            raise ValueError("环境配置文件过大")
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.strip().removeprefix("export ").partition("=")
            key = key.strip()
            if not separator or key not in allowed or os.environ.get(key):
                continue
            value = value.strip()
            if value[:1] in {"'", '"'}:
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError("环境配置引号不完整")
                value = value[1:-1]
            if value and "\x00" not in value:
                os.environ[key] = value
