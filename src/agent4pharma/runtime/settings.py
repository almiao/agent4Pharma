"""应用启动配置：校验采集窗口、粗筛快照和模型参数，生成可复跑的策略快照。"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, JsonValue, PrivateAttr, model_validator


class ModelSettings(BaseModel):
    """模型地址、显式型号与调用限制；不包含凭证，不自动切换供应商。"""

    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    timeout_seconds: float = Field(default=180, gt=0)
    max_tokens: int = Field(default=4096, gt=0)


class LocalTriageSettings(BaseModel):
    """本机预选显式启用与冻结身份，不包含外部服务凭证。"""

    enabled: bool = False
    endpoint: str = "http://127.0.0.1:18895"
    model_revision: str = "9ff7cf9838e59df461c276b7699d56b244d2af58"
    timeout_seconds: float = Field(default=120, gt=0)
    policy_json: str = ""


class ApplicationSettings(BaseModel):
    """单机发现应用配置，负责校验、路径解析、策略指纹与业务日期映射。"""

    policy_version: str = "agent-llm-pharma-v1"
    screening_json: str = ""
    intelligence_policy_ref: str = ""
    intelligence_model: str = ""
    intelligence_endpoint: str = ""
    intelligence_max_tokens: int = 4096
    intelligence_budget: int = Field(default=30, ge=1, le=500)
    paper_shortlist_limit: int = Field(default=5, ge=1, le=9)
    paused_journals: list[str] = Field(default_factory=list)
    timezone: str = "Asia/Shanghai"
    overlap_days: int = Field(default=3, ge=0)
    initial_days: int = Field(default=7, ge=1)
    page_budget: int = Field(default=50, ge=1)
    data_dir: Path = Path("data")
    llm: ModelSettings = Field(default_factory=ModelSettings)
    triage: LocalTriageSettings = Field(default_factory=LocalTriageSettings)
    sources: dict[str, dict[str, str]] = Field(default_factory=dict)
    _frozen_policy: dict[str, JsonValue] | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def reject_sampling(cls, values: object) -> object:
        """旧抽样参数明确拒绝，避免配置看似生效但实际被忽略。"""
        if isinstance(values, dict) and ({"sample_rate", "sample_seed"} & values.keys()):
            raise ValueError("随机抽样已移除，请删除 sample_rate/sample_seed，改用粗筛和情报预算")
        return values

    @classmethod
    def load(cls, path: Path) -> ApplicationSettings:
        """读取并校验 TOML 配置，解析相对数据目录并应用显式环境覆盖；无效配置抛错。"""
        path = path.resolve()
        with path.open("rb") as stream:
            config = cls.model_validate(tomllib.load(stream))
        # Relative paths belong to the project containing config/, not the caller's cwd.
        if not config.data_dir.is_absolute():
            config.data_dir = path.parent.parent / config.data_dir
        config.llm.base_url = os.getenv("DEEPSEEK_BASE_URL", config.llm.base_url)
        config.llm.model = os.getenv("DEEPSEEK_MODEL", config.llm.model)
        ZoneInfo(config.timezone)
        if not config.sources or set(config.sources) - {"pubmed", "arxiv", "biorxiv", "medrxiv"}:
            raise ValueError("Configure at least one supported source")
        return config

    def snapshot(self) -> dict:
        # Model credentials are never included. Acquisition and screening settings are immutable per run.
        """返回决定采集与筛选身份的策略快照，不包含密钥和可变执行预算。"""
        if self._frozen_policy is not None:
            return deepcopy(self._frozen_policy)
        result = {
            **self.model_dump(mode="json", exclude={"data_dir", "llm", "page_budget", "intelligence_budget"}),
            "acquisition_contract": "inclusive-source-date-v1",
        }
        if not self.triage.enabled:
            result.pop("triage", None)
        if not self.paused_journals:
            result.pop("paused_journals", None)
        return result

    def freeze_snapshot(self, snapshot: dict[str, JsonValue]) -> None:
        """续跑显式绑定原始策略身份，保留已停用字段但不恢复其执行能力。"""
        self._frozen_policy = deepcopy(snapshot)

    def fingerprint(self) -> str:
        """返回策略快照的稳定摘要，用于区分逻辑运行；执行重试不应改变它。"""
        return hashlib.sha256(json.dumps(self.snapshot(), sort_keys=True).encode()).hexdigest()[:16]

    def yesterday(self, now: datetime | None = None) -> date:
        """按配置业务时区取得昨日；传入 now 可用于确定性测试。"""
        return (now or datetime.now(ZoneInfo(self.timezone))).astimezone(
            ZoneInfo(self.timezone)
        ).date() - timedelta(days=1)

    def window(self, day: date) -> tuple[datetime, datetime]:
        """把业务日映射为带时区的半开区间 [开始日零点, 次日零点)。"""
        start = datetime.combine(day, time(), ZoneInfo(self.timezone))
        return start, start + timedelta(days=1)
