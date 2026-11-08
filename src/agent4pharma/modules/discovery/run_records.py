"""批次运行的只读契约：来源覆盖、来源项和分流分别保留，不按模型缓存数代替文章数。"""

from typing import Literal

from pydantic import BaseModel, Field

from agent4pharma.modules.collection.acquisition_interfaces import CollectionDiagnostic
from agent4pharma.modules.sources.management import SourceCollectionAvailability
from agent4pharma.modules.sources.source_categories import SourceCategory


class RunSource(BaseModel):
    """一个实际来源任务及其覆盖；未启动来源没有取得量，成功空来源取得量为零。"""

    source_id: str
    name: str = ""
    category: SourceCategory = "unknown"
    job_id: str = ""
    status: str
    records: int | None = None
    unique_materials: int | None = None
    error: str = ""
    start: str = ""
    end: str = ""
    attempt: int = 0
    request_url: str = ""
    request_url_basis: str = "unknown"
    updated_at: str = ""
    collection_mode: str = ""
    diagnostic: CollectionDiagnostic | None = None
    collection_availability: SourceCollectionAvailability | None = None


class RunArticle(BaseModel):
    """一个批次来源版本，失败不覆盖路由；重复资料可在多个来源各有一项。"""

    id: str
    material_id: str
    source_id: str
    title: str
    url: str = ""
    state: str
    member_kind: str
    category: SourceCategory = "unknown"
    triage_id: str = ""
    route: str = ""
    triage_error: str = ""
    triage_method: Literal["local_model", "keyword_gate", "semantic_passthrough", "unknown"] = "unknown"
    intelligence_task_id: str = ""
    duplicate_kind: Literal["", "already_assessed", "same_material"] = ""
    duplicate_of: str = ""
    duplicate_reason: str = ""
    ignore_recommended: bool = False


class DiscoveryRunSnapshot(BaseModel):
    """同一批次的完整成员清单与来源覆盖，不含正文或任意 SQL 查询能力。"""

    selected: str = ""
    aggregate_batch: str = ""
    batch_kind: str = ""
    batches: list[str] = Field(default_factory=list)
    sources: list[RunSource] = Field(default_factory=list)
    articles: list[RunArticle] = Field(default_factory=list)
