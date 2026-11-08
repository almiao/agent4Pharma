"""为官方 MinerU CLI 注入同一应用资料库路径；不修改全局环境或用户配置。"""

import os
from pathlib import Path


def mineru_service_home(root: Path) -> Path:
    """运行台的独立资料库目录，由装配入口传给查询、控制及缓存读取适配器。"""
    return root.resolve() / "services" / "mineru"


def mineru_environment(home: Path | None = None) -> dict[str, str]:
    """仅生成子进程环境；复用已有配置与权重，禁止隐式联网下载模型。"""
    environment = {**os.environ, "MINERU_TELEMETRY_DISABLED": "1", "PYTHONIOENCODING": "utf-8"}
    if home is not None:
        home = home.resolve()
        environment["MINERU_HOME"] = str(home)
        source_config = Path.home() / ".mineru" / "config.yaml"
        if source_config.is_file():
            environment.setdefault("MINERU_CONFIG", str(source_config))
        environment.update(
            {
                "MINERU_DOCLIB_DATA_DIR": str(home / "doclib"),
                "MINERU_DOCLIB_SQLITE_PATH": str(home / "doclib.db"),
                "MINERU_DOCLIB_ENDPOINT_PATH": str(home / "doclib.endpoint.json"),
                "MINERU_DOCLIB_LOG_DIR": str(home / "logs"),
                "MINERU_MODEL_BASE_DIR": str(home / "models"),
                "MINERU_MODEL_SOURCE": "local",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            }
        )
    return environment
