"""逐篇全文获取结果契约，下载可用性与主题判断、来源覆盖分开表达。"""

from typing import Literal, Protocol

from pydantic import BaseModel, Field, JsonValue


class PaperDownloadRequest(BaseModel):
    """已获授权的单篇资料快照；不能由模型指定文件路径或访问策略。"""

    batch_id: str
    decision_id: str
    material_ref: str
    source_id: str
    source_record_id: str
    title: str
    journal: str
    url: str
    identifier: str = ""


class PaperDownloadResult(BaseModel):
    """一次真实下载尝试；失败只描述当前环境和时间，不表示永久不可下载。"""

    id: str
    request: PaperDownloadRequest
    status: Literal[
        "downloaded",
        "identity_unverified",
        "ancillary_document",
        "failed",
        "timeout",
        "unresolved",
        "invalid_pdf",
        "manual_required",
        "unavailable",
        "cooldown",
    ]
    identifier: str = ""
    reason: str = ""
    channel: str = ""
    file: str = ""
    text_file: str = ""
    sha256: str = ""
    text_sha256: str = ""
    bytes: int = 0
    pages: int = 0
    text_characters: int = 0
    text_scope: Literal["legacy_fulltext", "identity_first_page"] = "legacy_fulltext"
    identity_match: bool = False
    attempts: list[JsonValue] = Field(default_factory=list)
    runtime_profile: dict[str, JsonValue] = Field(default_factory=dict)
    plugin_version: str = "chaoslib-v1"
    plugin_revision: str = ""
    policy: Literal["legal_only"] = "legal_only"
    operation: Literal["download", "revalidate"] = "download"
    created_at: str


class PaperDownloader(Protocol):
    """单篇下载能力，不拥有选择资料或扩充预算的权限。"""

    def download(self, request: PaperDownloadRequest) -> PaperDownloadResult:
        """返回真实尝试或受限状态；网络不在数据库事务内。"""
        ...


class PaperDownloadLedger(Protocol):
    """资料模块公开的追加下载账本。"""

    def save(self, result: PaperDownloadResult) -> None:
        """追加一次不可覆盖的结果。"""
        ...

    def results(self, batch_id: str = "") -> list[PaperDownloadResult]:
        """读取明确批次每份请求的最新结果。"""
        ...
